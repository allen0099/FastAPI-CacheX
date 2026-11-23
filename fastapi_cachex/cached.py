"""``@cached``: cache the result of a plain function through ``CacheManager``."""

import functools
import hashlib
import inspect
import json
import logging
import sys
import types
import typing
import warnings
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from datetime import timedelta
from functools import wraps
from typing import Any
from typing import Concatenate
from typing import Generic
from typing import ParamSpec
from typing import Protocol
from typing import TypeVar
from typing import overload

from pydantic import BaseModel
from pydantic_core import PydanticSerializationError
from pydantic_core import to_jsonable_python

from ._tags import validate_tags
from .backends.base import validate_ttl
from .dependencies import get_app_cache
from .exceptions import CacheXError
from .manager import _LEAVES
from .manager import CacheManager
from .manager import _adapter
from .manager import _holds_a_secret

logger = logging.getLogger(__name__)

P = ParamSpec("P")
Q = ParamSpec("Q")
R = TypeVar("R")

# Separates the function's name from the digest of its arguments in a default
# key, as `CacheManager` keys separate their parts.
_KEY_SEPARATOR = ":"


# `_model_json` could not vouch for the argument: search it and encode it the
# general way.
_UNCHECKED: Any = object()

# Core schema types that hold only values that cannot be a secret, or other
# schemas walked in turn. Validators (`function-before` / `-after`) do not
# matter: a value they return that does not fit the field's type fails the
# `warnings="error"` serialization instead.
_PLAIN_SCHEMAS = frozenset(
    {
        "model", "model-fields", "model-field", "computed-field",
        "dataclass", "dataclass-args", "dataclass-field",
        "typed-dict", "typed-dict-field", "list", "set", "frozenset", "tuple",
        "dict", "nullable", "default", "definitions", "definition-ref",
        "union", "tagged-union", "chain", "lax-or-strict", "literal", "enum",
        "none", "bool", "int", "float", "decimal", "complex", "str", "bytes",
        "date", "time", "datetime", "timedelta", "uuid", "url",
        "multi-host-url", "function-before", "function-after", "no-info",
        "with-info",
    }
)  # fmt: skip
_CUSTOM_SERIALIZERS = frozenset({"function-plain", "function-wrap"})


def _node_may_hold_a_secret(schema: dict[str, Any]) -> bool:
    """Whether a core schema node, apart from its children, can hold a secret.

    True for ``any``, ``is-instance`` (a secret's own schema), a plain or wrap
    validator or serializer (they take or return anything), extra fields
    kept with ``extra="allow"``, an enum with a member that is not plain,
    and a schema type not known to be plain.
    """
    kind = schema.get("type")
    serialization = schema.get("serialization")
    return (
        (kind is not None and kind not in _PLAIN_SCHEMAS)
        or schema.get("config", {}).get("extra_fields_behavior") == "allow"
        or schema.get("extra_behavior") == "allow"
        or (
            kind == "enum"
            and not _LEAVES.issuperset(
                type(member.value) for member in schema["members"]
            )
        )
        or (
            isinstance(serialization, dict)
            and serialization.get("type") in _CUSTOM_SERIALIZERS
        )
    )


def _schema_is_plain(schema: object) -> bool:
    """Whether a core schema can hold no value but plain data."""
    if isinstance(schema, list):
        return all(_schema_is_plain(item) for item in schema)
    if not isinstance(schema, dict):
        return True
    if schema.get("type") == "model-field" and schema.get("serialization_exclude"):
        return True  # not written out, so not in the key
    return not _node_may_hold_a_secret(schema) and all(
        _schema_is_plain(item)
        for key, item in schema.items()
        if key not in ("metadata", "serialization", "members", "expected")
    )


@functools.lru_cache(maxsize=256)
def _plain_model(kind: type[BaseModel]) -> bool:
    """Whether instances of a pydantic model can hold no secret (#668)."""
    try:
        return _schema_is_plain(kind.__pydantic_core_schema__)
    except Exception:  # noqa: BLE001 - a schema not built yet, or unusual
        return False


def _model_json(value: object) -> Any:
    """A model argument's JSON form, if its type rules out a secret, else ``_UNCHECKED``.

    The model's own serializer gives what ``to_jsonable_python`` gives
    (``by_alias=True`` as it defaults), so keys are unchanged. ``warnings="error"``
    makes it fail for a value that does not fit its field's type, such as a
    secret put into a ``str`` field by ``model_construct()``: that argument
    is then searched like any other. A model whose fields can hold a secret
    is searched too. This spares the search for most models on every call,
    hits included.
    """
    serializer = getattr(value, "__pydantic_serializer__", None)
    if serializer is None or not isinstance(value, BaseModel):
        return _UNCHECKED
    if not _plain_model(type(value)):
        return _UNCHECKED
    try:
        return serializer.to_python(value, mode="json", by_alias=True, warnings="error")
    except Exception:  # noqa: BLE001 - a misfit value, or anything unusual
        return _UNCHECKED


def _jsonable_form(value: object) -> object:
    """``value`` as JSON-ready data for its key; a set is sorted."""
    kind = type(value)
    if isinstance(value, Iterator):
        # The function would then run on an empty generator, and that
        # result be cached.
        msg = f"Object of type {kind.__name__} is an iterator, not hashable as a key"
        raise TypeError(msg)
    if isinstance(value, (set, frozenset)):
        # Each item as canonical JSON, sorted: set order follows string
        # hashes, which differ between processes. Nested sets go through here
        # too. A set inside a dataclass or a model is not sorted.
        return sorted(
            json.dumps(
                item, sort_keys=True, separators=(",", ":"), default=_jsonable_argument
            )
            for item in value
        )
    try:
        return to_jsonable_python(value, bytes_mode="base64")
    except PydanticSerializationError as e:
        msg = f"Object of type {kind.__name__} is not JSON serializable"
        raise TypeError(msg) from e


def _jsonable_argument(value: object) -> object:
    """An argument ``json`` cannot encode, as JSON tagged with its type (#577).

    ``datetime``, ``Decimal``, ``UUID``, ``bytes`` (as base64), a set, a
    dataclass or a pydantic model, through pydantic's JSON form. The tag
    keeps ``datetime(2024, 1, 1)`` and the string ``"2024-01-01T00:00:00"``
    apart, and a set is sorted, so its key is the same in every process.
    Arguments ``json`` encodes on its own never get here, so their keys are
    unchanged.

    Raises:
        TypeError: If pydantic cannot encode it either (an arbitrary object),
            it is an iterator, which encoding it would use up, or it holds a
            secret, which pydantic encodes as its mask
    """
    kind = type(value)
    encoded = _model_json(value)
    if encoded is _UNCHECKED:
        if _holds_a_secret(value):
            # pydantic writes a secret as "**********", so calls that differ
            # only in it would share one key, and one caller get another's
            # result (#654).
            msg = (
                f"Object of type {kind.__name__} holds a pydantic secret "
                "(SecretStr, SecretBytes or Secret), which would be keyed as "
                "its mask"
            )
            raise TypeError(msg)
        encoded = _jsonable_form(value)
    return {"$type": f"{kind.__module__}.{kind.__qualname__}", "value": encoded}


def _optional_signature(func: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return _signature(func)
    except (TypeError, ValueError, NameError):
        return None


def _call_with_defaults(
    func: Callable[..., Any],
    signature: inspect.Signature | None,
    bound: inspect.BoundArguments,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Call a ``key=`` or ``tags=`` callable with the call's arguments, defaults applied.

    Given only what the caller passed, ``key=lambda user_id, locale="fr": ...``
    named a call to ``def load(user_id, locale="en")`` after a locale it did not
    use, and ``load(1)`` and ``load(1, "en")`` stored one result twice (#577).
    A callable that cannot take every argument (``key=lambda user_id: ...``
    there) still gets only the caller's.
    """
    if signature is not None:
        try:
            signature.bind(*bound.args, **bound.kwargs)
        except TypeError:
            pass
        else:
            return func(*bound.args, **bound.kwargs)
    return func(*args, **kwargs)


class _Unset:
    """``@cached(type_=...)`` not passed, as opposed to ``type_=None``."""

    def __repr__(self) -> str:
        """Shown as the default in the signature and the API reference."""
        return "<not passed>"


_UNSET: Any = _Unset()

# A return annotation that is there but cannot be evaluated where the
# decorator is applied.
_UNRESOLVED: Any = object()

_JSON_SCALARS = (str, int, float, bool, type(None), object)

_UNRESOLVED_ANNOTATION_WARNING = (
    "cached return annotation: the annotations of {name}() cannot be resolved "
    "where @cached is applied (a name imported under TYPE_CHECKING, or a "
    "class defined further down), so @cached cannot validate the cached "
    "value into the return type and this function returns the JSON form. "
    "Pass type_=<the type> to get the type, or type_=None to keep the JSON "
    "form without this warning."
)

_UNSUPPORTED_ANNOTATION_WARNING = (
    "cached return annotation: {name}() is annotated to return {annotation}, "
    "a type pydantic cannot validate, so @cached cannot validate the cached "
    "value into it and this function returns the JSON form. Pass type_=None "
    "to keep the JSON form without this warning, or type_=<a type pydantic "
    "can validate>."
)


def _return_annotation(func: Callable[..., Any]) -> Any:
    """The function's resolved return annotation.

    ``_UNSET`` when there is none, and ``_UNRESOLVED`` when there is one but
    it cannot be evaluated here (a forward reference to a name defined later,
    an import under ``TYPE_CHECKING``). A parameter's annotation is never
    evaluated: one that cannot be resolved says nothing about the return type.
    """
    annotations = _raw_annotations(func)
    if "return" not in annotations:
        return _UNSET

    def stub() -> None: ...

    stub.__annotations__ = {"return": annotations["return"]}
    namespace = getattr(inspect.unwrap(func), "__globals__", None)
    try:
        hints = typing.get_type_hints(stub, globalns=namespace, include_extras=True)
        return hints["return"]
    except Exception:  # noqa: BLE001 - whatever evaluating an annotation raises
        return _UNRESOLVED


def _signature(func: Callable[..., Any]) -> inspect.Signature:
    """``inspect.signature`` without evaluating the annotations.

    Only the parameters are used, to bind a call's arguments. From Python
    3.14 annotations are evaluated lazily, and ``inspect.signature`` would
    raise ``NameError`` for a name imported under ``TYPE_CHECKING``.
    """
    if sys.version_info >= (3, 14):
        import annotationlib  # noqa: PLC0415 - not there before 3.14

        return inspect.signature(
            func, annotation_format=annotationlib.Format.FORWARDREF
        )
    return inspect.signature(func)


def _raw_annotations(func: Callable[..., Any]) -> dict[str, Any]:
    """The annotations of ``func`` as written, without evaluating them."""
    if sys.version_info >= (3, 14):
        # Annotations are evaluated lazily: reading __annotations__ would
        # raise for the very annotation that could not be resolved.
        import annotationlib  # noqa: PLC0415 - not there before 3.14

        return annotationlib.get_annotations(
            func, format=annotationlib.Format.FORWARDREF
        )
    return getattr(func, "__annotations__", {})


def _is_json_shaped(annotation: Any, aliases: tuple[Any, ...] = ()) -> bool:
    """Whether validating a JSON value into ``annotation`` could not change it.

    ``dict[str, Any]``, ``list[int]``, ``str | None`` and the like, built
    only from JSON's own types. ``@cached`` returns such a value as
    ``json.loads`` gave it and never validates it, so an ``int`` returned by
    a function annotated ``-> float`` stays an ``int``.
    """
    while typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]  # the metadata is not applied
    if isinstance(annotation, _TYPE_ALIAS):
        return _is_json_alias(annotation, aliases)
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if (
        annotation is Any
        or annotation in _JSON_SCALARS
        or annotation in (list, dict)
        or (origin in (list, dict) and not args)  # typing.List, typing.Dict
    ):
        return True
    if origin is list:
        return all(_is_json_shaped(arg, aliases) for arg in args)
    if origin is dict:
        return args[0] is str and _is_json_shaped(args[1], aliases)
    if origin in (typing.Union, types.UnionType):
        return all(_is_json_shaped(arg, aliases) for arg in args)
    return False


# ``type Json = dict[str, Json] | None`` (Python 3.12+).
_TYPE_ALIAS: Any = getattr(typing, "TypeAliasType", ())


def _is_json_alias(alias: Any, aliases: tuple[Any, ...]) -> bool:
    """``_is_json_shaped`` for an alias, which is lazy and may name itself."""
    if alias in aliases:
        return True
    try:
        return _is_json_shaped(alias.__value__, (*aliases, alias))
    except NameError:
        return False


def _type_from_annotation(func: Callable[..., Any]) -> Any:
    """The type ``@cached`` validates into when ``type_`` is not passed (#533).

    The return annotation, or ``None`` (no validation) when there is none or
    the JSON form already is that type. One that cannot be resolved, or that
    pydantic cannot validate (``Iterator[int]``, a ``Response``, a plain
    class), also gives ``None``, with a warning: the function would otherwise
    differ from its neighbours without a sign, or raise on every call.
    """
    annotation = _return_annotation(func)
    if annotation is _UNSET or _is_json_shaped(annotation):
        return None
    if annotation is _UNRESOLVED:
        message = _UNRESOLVED_ANNOTATION_WARNING.format(name=func.__qualname__)
    else:
        try:
            _adapter(annotation)
        except Exception:  # noqa: BLE001 - whatever pydantic raises for the type
            message = _UNSUPPORTED_ANNOTATION_WARNING.format(
                name=func.__qualname__, annotation=annotation
            )
        else:
            return annotation
    # stacklevel: the line that applies the decorator, cached(...)(func).
    warnings.warn(message, UserWarning, stacklevel=3)
    return None


class CachedFunction(Generic[P, R]):
    """What ``@cached`` turns a function into.

    Calling it is always ``await``-ed, whether the function was ``async`` or
    not, and returns the cached value: on a miss the function runs through
    ``CacheManager.get_or_set()``, with its stampede protection, and its
    result is stored. ``cache_key()`` and ``invalidate()`` take the same
    arguments as the function.
    """

    # Copied from the function by `functools.wraps`.
    __name__: str
    __qualname__: str

    def __init__(  # noqa: PLR0913
        self,
        func: Callable[P, Awaitable[R]] | Callable[P, R],
        *,
        ttl: int | None,
        key: str | Callable[P, str] | None,
        manager: CacheManager | None,
        lock: bool | None,
        type_: Any = None,
        tags: Iterable[str] | Callable[..., Iterable[str]] | None = None,
        stale_ttl: int | None = None,
    ) -> None:
        """Wrap ``func``; ``cached()`` builds these."""
        self._stale_ttl = stale_ttl
        self.__wrapped__ = func
        self._signature = _signature(func)
        self._ttl = ttl
        self._key = key
        self._key_signature = _optional_signature(key) if callable(key) else None
        self._manager = manager
        self._lock = lock
        self._type = type_
        # A callable is asked on every call; a list is checked once, here.
        self._tags = tags if callable(tags) else validate_tags(tags)
        self._tags_signature = _optional_signature(tags) if callable(tags) else None
        self._name = f"{func.__module__}.{func.__qualname__}"
        wraps(func)(self)

    @property
    def manager(self) -> CacheManager:
        """The ``CacheManager`` the values go through.

        The one given to ``@cached``, or else the application's (what the
        ``AppCache`` dependency returns), resolved on every call so a manager
        registered with ``CacheManagerProxy.set()`` at startup is used.
        """
        if self._manager is not None:
            return self._manager
        return get_app_cache()

    @overload
    def __get__(
        self, instance: None, owner: type | None = None
    ) -> "CachedFunction[P, R]": ...
    @overload
    def __get__(
        self: "CachedFunction[Concatenate[Any, Q], R]",
        instance: object,
        owner: type | None = None,
    ) -> "_BoundCachedFunction[Q, R]": ...
    @overload
    def __get__(
        self, instance: object, owner: type | None = None
    ) -> "_BoundCachedFunction[..., R]": ...
    def __get__(
        self, instance: object | None, owner: type | None = None
    ) -> "CachedFunction[P, R] | _BoundCachedFunction[Any, R]":
        """Bind ``instance`` as the first argument, so methods work.

        ``obj.load(1)``, ``obj.load.cache_key(1)`` and ``obj.load.invalidate(1)``
        all pass ``obj`` as ``self``; a default key then needs ``key=`` since
        ``obj`` is not JSON-serializable.
        """
        if instance is None:
            return self
        return _BoundCachedFunction(self, instance)

    def cache_key(self, *args: P.args, **kwargs: P.kwargs) -> str:
        """The key a call with these arguments reads and writes.

        Without ``key`` it is ``module.qualname:<sha256 of the arguments>``:
        the arguments are bound to the function's signature, defaults
        applied, so a value passed by position or by name, or left to its
        default, gives the same key. They are hashed as JSON, so they must be
        JSON-serializable; ``@cached(key=...)`` names the key for anything
        else (a method's ``self``, a model). A ``str`` ``key`` is a
        ``str.format`` template over the bound arguments; a callable is called
        with them. The manager's ``key_prefix`` is not part of it.

        Raises:
            CacheXError: If an argument is not JSON-serializable and no
                ``key`` was given, a ``key`` template names an argument the
                function does not have, or ``key`` does not give a ``str``
            TypeError: If the arguments do not fit the function's signature
        """
        return self._key_for(self._bind(args, kwargs), args, kwargs)

    def _bind(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> inspect.BoundArguments:
        bound = self._signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return bound

    def _key_for(
        self,
        bound: inspect.BoundArguments,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        if self._key is None:
            return self._default_key(bound.arguments)
        if isinstance(self._key, str):
            try:
                return self._key.format(**bound.arguments)
            except (KeyError, IndexError, AttributeError, ValueError, TypeError) as e:
                msg = (
                    f"@cached key template {self._key!r} for {self._name} names "
                    f"something its arguments do not have ({e!r})"
                )
                raise CacheXError(msg) from e
        key = _call_with_defaults(self._key, self._key_signature, bound, args, kwargs)
        if not isinstance(key, str):
            msg = f"key must return a str, got {type(key).__name__}"
            raise CacheXError(msg)
        return key

    def _default_key(self, arguments: dict[str, Any]) -> str:
        try:
            canonical = json.dumps(
                arguments,
                sort_keys=True,
                separators=(",", ":"),
                default=_jsonable_argument,
            )
        except (TypeError, ValueError) as e:
            msg = (
                f"@cached cannot build a key for {self._name}: an argument is not "
                f"JSON-serializable ({e}). Pass key= to name the key yourself."
            )
            raise CacheXError(msg) from e
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{self._name}{_KEY_SEPARATOR}{digest}"

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """Return the cached value, running the function on a miss."""
        bound = self._bind(args, kwargs)
        key = self._key_for(bound, args, kwargs)

        def factory() -> Awaitable[R] | R:
            return self.__wrapped__(*args, **kwargs)

        # Passed only when given: a manager subclass may override
        # get_or_set() with the signature it had before 0.5.5.
        extra: dict[str, Any] = {} if self._type is None else {"type_": self._type}
        if self._tags:
            extra["tags"] = (
                _call_with_defaults(
                    self._tags, self._tags_signature, bound, args, kwargs
                )
                if callable(self._tags)
                else self._tags
            )
        if self._stale_ttl is not None:
            extra["stale_ttl"] = self._stale_ttl
        value: R = await self.manager.get_or_set(
            key, factory, ttl=self._ttl, lock=self._lock, **extra
        )
        return value

    async def invalidate(self, *args: P.args, **kwargs: P.kwargs) -> bool:
        """Remove the value cached for a call with these arguments.

        Returns:
            Whether a value was cached for them
        """
        removed = await self.manager.delete(self.cache_key(*args, **kwargs))
        logger.debug("@cached INVALIDATE; function=%s removed=%s", self._name, removed)
        return removed


class _BoundCachedFunction(Generic[Q, R]):
    """A ``CachedFunction`` looked up on an instance (see ``__get__``).

    ``Q`` is the method's parameters after ``self``, so a type checker checks
    ``obj.load(1)`` as it checks a plain function's call (#580).
    """

    def __init__(self, function: "CachedFunction[Any, R]", instance: object) -> None:
        """Bind ``instance`` to ``function``."""
        self.__wrapped__ = function
        self.__self__ = instance

    def cache_key(self, *args: Q.args, **kwargs: Q.kwargs) -> str:
        """``CachedFunction.cache_key`` with the instance as ``self``."""
        return self.__wrapped__.cache_key(self.__self__, *args, **kwargs)

    async def __call__(self, *args: Q.args, **kwargs: Q.kwargs) -> R:
        """``CachedFunction.__call__`` with the instance as ``self``."""
        return await self.__wrapped__(self.__self__, *args, **kwargs)

    async def invalidate(self, *args: Q.args, **kwargs: Q.kwargs) -> bool:
        """``CachedFunction.invalidate`` with the instance as ``self``."""
        return await self.__wrapped__.invalidate(self.__self__, *args, **kwargs)


class _Decorator(Protocol):
    @overload
    def __call__(self, func: Callable[P, Awaitable[R]]) -> CachedFunction[P, R]: ...
    @overload
    def __call__(self, func: Callable[P, R]) -> CachedFunction[P, R]: ...


def cached(  # noqa: PLR0913
    ttl: int | timedelta | None = None,
    *,
    key: str | Callable[..., str] | None = None,
    manager: CacheManager | None = None,
    lock: bool | None = None,
    type_: Any = _UNSET,
    tags: Iterable[str] | Callable[..., Iterable[str]] | None = None,
    stale_ttl: int | timedelta | None = None,
) -> _Decorator:
    """Cache what a plain function returns, keyed on its arguments.

    For a function that is not a route: a loader, a slow computation, a call
    to another service. The decorated function is always ``await``-ed, even
    if it was ``def``, and returns the cached value on a hit; on a miss it
    runs through ``CacheManager.get_or_set()``, so concurrent misses of one
    key run it once (the manager's stampede protection), and its result is
    stored. A sync function runs on the event loop, as a ``get_or_set``
    factory does; wrap blocking I/O in ``run_in_threadpool`` yourself.

    The value goes through the manager's JSON round-trip and is then
    validated into the function's return annotation, so a function annotated
    ``-> User`` returns a ``User`` and one annotated ``-> tuple[int, int]`` a
    tuple, on the first call and on a hit. Without an annotation, or with
    ``type_=None``, the function returns what JSON gives back (a tuple as a
    list, integer dict keys as strings, a pydantic model as a ``dict``).

    The decorated function has ``cache_key(*args, **kwargs)`` for the key a
    call uses and ``await invalidate(*args, **kwargs)`` to drop its value.

    Args:
        ttl: How long a value is cached, in seconds or as a ``timedelta``.
            ``None`` uses the manager's ``default_ttl``.
        key: How to name the key, without the manager's prefix. ``None``
            hashes the arguments: ``module.qualname:<sha256>``, which needs
            JSON-serializable arguments (a method's ``self`` is not). A
            ``str`` is a ``str.format`` template over the arguments by name,
            defaults applied (``"user:{user_id}"``); a plain string is a
            fixed key. A callable gets the call's arguments and returns the
            key (``lambda self, user_id: f"user:{user_id}"``).
        manager: The ``CacheManager`` to store through. ``None`` uses the
            application's, what the ``AppCache`` dependency returns,
            resolved on each call.
        lock: Whether concurrent misses take the manager's distributed lock.
            ``None`` uses the manager's ``lock`` setting.
        stale_ttl: Keep a value this much longer than ``ttl`` and use it
            past ``ttl`` as a stale value: one call refreshes it while the
            others get it at once, and it is returned when the function
            raises. As for ``get_or_set()``; needs a ``ttl`` (or the
            manager's ``default_ttl``).
        type_: The type to validate the cached value into, usually the
            function's return type (``@cached(ttl=60, type_=User)``,
            ``type_=list[User]``), as for ``CacheManager.get(type_=...)``.
            A cached value that does not fit it counts as a miss and the
            function runs again. ``None`` returns the JSON form. Not passing
            it uses the function's return annotation, read when the
            decorator is applied; a missing annotation, or one built only
            from JSON's own types (``dict[str, Any]``, ``list[int]``,
            ``str | None``), means no validation. Before 0.6.0 not passing
            it always returned the JSON form.
        tags: Names the cached values can be invalidated by, with
            ``invalidate_tags()``: a list of strings, or a function that
            takes the same arguments as the decorated one and returns them
            (``tags=lambda user_id: [f"user:{user_id}"]``).

    Returns:
        A decorator turning the function into a ``CachedFunction``

    Warns:
        UserWarning: When the decorator is applied, if ``type_`` is not
            passed and the function's annotations cannot be resolved there
            (a type imported under ``TYPE_CHECKING``, a class defined
            further down). The function then returns the JSON form.

    Raises:
        TTLTypeError: When the decorator is applied, if ``ttl`` is not an
            ``int``, a ``timedelta`` or ``None`` (also a ``TypeError``)
        TTLValueError: When the decorator is applied, if ``ttl`` is zero,
            negative, larger than ``MAX_TTL`` or not a whole number of
            seconds (also a ``ValueError``)
        TypeError: When the decorator is applied, if ``lock`` is not a
            ``bool`` or ``None``, or the function is a generator function
        CacheXError: When the decorator is applied, if ``key`` is not a
            ``str``, a callable or ``None``; and when the function is
            called, if the key cannot be built (see ``cache_key``)
    """
    ttl_seconds = validate_ttl(ttl)
    stale_seconds = validate_ttl(stale_ttl)
    if lock is not None and not isinstance(lock, bool):
        msg = f"lock must be a bool or None, got {type(lock).__name__}"  # type: ignore[unreachable]
        raise TypeError(msg)
    if key is not None and not isinstance(key, str) and not callable(key):
        msg = f"key must be a str, a callable or None, got {type(key).__name__}"  # type: ignore[unreachable]
        raise CacheXError(msg)

    def decorator(func: Callable[P, Any]) -> CachedFunction[P, Any]:
        # The function itself, not what it wraps: a `functools.wraps`
        # decorator that collects a generator into a list returns a list.
        if inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func):
            # Each call ran the body and then failed to store a generator.
            msg = (
                f"@cached cannot cache {getattr(func, '__qualname__', func)!r}: it is a generator "
                "function, whose result cannot be stored. Cache a function "
                "that returns a list instead."
            )
            raise TypeError(msg)
        return CachedFunction(
            func,
            ttl=ttl_seconds,
            key=key,
            manager=manager,
            lock=lock,
            type_=_type_from_annotation(func) if type_ is _UNSET else type_,
            tags=tags,
            stale_ttl=stale_seconds,
        )

    return decorator


__all__ = ["CachedFunction", "cached"]
