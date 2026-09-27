"""Core caching functionality and decorators."""

import hashlib
import inspect
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from functools import update_wrapper
from functools import wraps
from inspect import Parameter
from inspect import Signature
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from typing import Literal
from typing import cast
from typing import get_args
from typing import get_origin
from typing import get_type_hints

from fastapi import Request
from fastapi import Response
from fastapi.encoders import jsonable_encoder
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import TypeAdapter
from starlette.concurrency import run_in_threadpool
from starlette.status import HTTP_200_OK
from starlette.status import HTTP_206_PARTIAL_CONTENT
from starlette.status import HTTP_300_MULTIPLE_CHOICES
from starlette.status import HTTP_304_NOT_MODIFIED

from .backends.base import MAX_TTL
from .directives import DirectiveType
from .exceptions import BackendNotFoundError
from .exceptions import CacheXError
from .exceptions import RequestNotFoundError
from .headers import add_vary
from .proxy import BackendProxy
from .proxy import get_backend_or_fallback
from .types import CACHE_KEY_SEPARATOR
from .types import CacheEntry
from .types import CacheKeyBuilder
from .types import escape_key_component

if TYPE_CHECKING:
    from fastapi.routing import APIRoute

# Handler callable accepted by @cache: can return any type (sync or async).
HandlerCallable = Callable[..., Awaitable[object]] | Callable[..., object]

# Wrapper callable produced by @cache: always async and returns Response.
AsyncResponseCallable = Callable[..., Awaitable[Response]]

logger = logging.getLogger(__name__)

_NO_STORE = DirectiveType.NO_STORE.value


def build_cache_key(request: Request, *components: str | int) -> str:
    """Build the default cache key for ``request``, plus extra components.

    With no ``components`` the key is ``method|||host|||path|||query_params``,
    exactly what ``@cache`` uses by default. Each extra component is appended
    after another separator, so a custom ``key_builder`` can add a dimension
    (user ID, tenant, locale) without rebuilding the default key by hand::

        def per_user_key(request: Request) -> str:
            return build_cache_key(request, request.state.user_id)

    ``|`` and ``%`` in the host, the path and every extra component are
    percent-encoded (see ``escape_key_component``), so none of them can
    contain the separator and make one request's key equal another's. The
    query string is already URL-encoded and never contains ``|``.

    Keys built this way keep the path in the third component, so
    ``clear_path()`` still finds them and the monitoring routes still show
    their method, host, path and query.

    Args:
        request: The FastAPI Request object
        *components: Extra key components, appended in order. A ``str`` is
            used as is and an ``int`` is written in decimal, so ``1`` and
            ``"1"`` give the same key. An empty string is a component of its
            own: ``build_cache_key(request, "")`` differs from
            ``build_cache_key(request)``.

    Returns:
        Generated cache key string

    Raises:
        TypeError: If a component is not a ``str`` or ``int`` (``bool`` is
            rejected too), e.g. ``None`` from a missing user ID, which would
            otherwise put every such caller under one ``"None"`` key.
    """
    key = _append_key_components(
        CACHE_KEY_SEPARATOR.join(
            [
                request.method,
                escape_key_component(request.headers.get("host", "unknown")),
                escape_key_component(request.url.path),
                str(request.query_params),
            ]
        ),
        components,
    )
    logger.debug("Built cache key: %s", key)
    return key


def _append_key_components(key: str, components: Sequence[str | int]) -> str:
    """Append each component to ``key``, escaped, after another separator."""
    parts = [key]
    for component in components:
        if isinstance(component, bool) or not isinstance(component, (str, int)):
            msg = (
                "build_cache_key components must be str or int, "
                f"got {type(component).__name__}"
            )
            raise TypeError(msg)
        parts.append(escape_key_component(str(component)))
    return CACHE_KEY_SEPARATOR.join(parts)


# RFC 9110 §5.1: a field name is a token.
_FIELD_NAME_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


def _validate_vary(vary: Sequence[str] | None) -> list[str]:
    """Check ``@cache(vary=...)`` and return the names, first spelling of each.

    Raises:
        CacheXError: If ``vary`` is a single string instead of a sequence of
            names, or a name is not a non-empty header field name, or is ``*``.
    """
    if vary is None:
        return []
    if isinstance(vary, (str, bytes)) or not isinstance(vary, Sequence):
        msg = (
            "vary must be a list of header names, e.g. vary=['Accept-Language'], "
            f"got {type(vary).__name__}"
        )
        raise CacheXError(msg)
    names: dict[str, str] = {}
    for name in vary:
        if not isinstance(name, str) or not name or not set(name) <= _FIELD_NAME_CHARS:
            msg = f"vary entries must be header field names, got {name!r}"
            raise CacheXError(msg)
        if name == "*":
            msg = "vary cannot contain '*': the key can only vary on named headers"
            raise CacheXError(msg)
        names.setdefault(name.lower(), name)
    return list(names.values())


def _vary_components(request: Request, names: Sequence[str]) -> list[str]:
    """The key components for ``@cache(vary=names)``: ``name=value`` each.

    The name is lower-cased and the value trimmed; repeated header lines are
    joined with ``,`` as RFC 9110 §5.3 allows, and a missing header gives an
    empty value, the same as an empty one.
    """
    return [
        f"{name.lower()}="
        + ",".join(value.strip() for value in request.headers.getlist(name))
        for name in names
    ]


def default_key_builder(request: Request) -> str:
    """Default cache key builder function: ``build_cache_key(request)``.

    Generates cache key in format: method|||host|||path|||query_params

    Kept as the name ``@cache`` and ``invalidate()`` fall back to. To add
    components to the default key, call ``build_cache_key`` instead.

    Args:
        request: The FastAPI Request object

    Returns:
        Generated cache key string
    """
    return build_cache_key(request)


async def invalidate(
    request: Request,
    key_builder: CacheKeyBuilder | None = None,
    vary: Sequence[str] | None = None,
) -> bool:
    """Invalidate the cache entry a ``@cache``-decorated route would use.

    Builds the same cache key the ``@cache`` decorator would build for
    ``request`` (via ``key_builder`` or ``default_key_builder``) and deletes
    it from the configured backend. Use this after a mutation to bust the
    cache for a specific cached route response.

    Args:
        request: The request whose cache key should be invalidated. Typically
            a request to the same route/method as the cached one (e.g. build
            it via ``request.app.url_path_for(...)`` for a GET route).
        key_builder: Custom key builder used by the target route's ``@cache``
            decorator, if any. If None, uses ``default_key_builder``.
        vary: The target route's ``vary`` names, if any. Only the variant
            selected by ``request``'s own values for those headers is
            deleted; ``clear_path()`` clears every variant of a path.

    Returns:
        True if a cache entry existed and was deleted, False otherwise.

    Raises:
        CacheXError: If ``vary`` is not a list of header names.
    """
    builder = key_builder or default_key_builder
    vary_names = _validate_vary(vary)
    cache_key = _append_key_components(
        builder(request), _vary_components(request, vary_names)
    )

    try:
        cache_backend = BackendProxy.get()
    except BackendNotFoundError:
        return False

    if await cache_backend.get_and_delete(cache_key) is None:
        return False
    logger.debug("Cache INVALIDATE; key=%s", cache_key)
    return True


class CacheControl:
    """Manages Cache-Control header directives."""

    def __init__(self) -> None:
        """Initialize an empty CacheControl instance."""
        self.directives: list[str] = []

    def add(self, directive: DirectiveType, value: int | None = None) -> None:
        """Add a Cache-Control directive.

        Args:
            directive: The directive type to add
            value: Optional value for the directive
        """
        if value is not None:
            self.directives.append(f"{directive.value}={value}")
        else:
            self.directives.append(directive.value)

    def __str__(self) -> str:
        """Return the Cache-Control header value as a string."""
        return ", ".join(self.directives)


# Headers that must never be replayed from cache. ``set-cookie`` carries
# per-user state, ``content-type`` is already held by ``CacheEntry.media_type``
# (storing both would emit the header twice), and the rest are either
# connection-scoped or rebuilt for every response.
_UNCACHEABLE_HEADERS = frozenset(
    {
        "set-cookie",
        "content-length",
        "transfer-encoding",
        "connection",
        "date",
        "etag",
        "cache-control",
        "content-type",
    }
)


def _is_cacheable_status(status_code: int) -> bool:
    """Whether a response with this status may be stored and replayed.

    Only successful responses are cacheable here. ``206 Partial Content`` is
    excluded because its body is meaningful only for the ``Range`` request that
    produced it, so replaying it to another request would corrupt the response.
    """
    return (
        HTTP_200_OK <= status_code < HTTP_300_MULTIPLE_CHOICES
        and status_code != HTTP_206_PARTIAL_CONTENT
    )


def _cacheable_headers(response: Response) -> dict[str, str] | None:
    """The handler's own headers worth storing, or None when there are none."""
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _UNCACHEABLE_HEADERS
    }
    return headers or None


# Fields RFC 9110 §15.4.5 asks a 304 to repeat from the 200 it stands in for.
# ``Date`` comes from Starlette, ``ETag`` and ``Cache-Control`` are set on the
# 304 directly, which leaves these three to be carried over.
_REVALIDATION_HEADERS = frozenset({"content-location", "expires", "vary"})


def _revalidation_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """The subset of a response's headers that a 304 must repeat."""
    if not headers:
        return {}
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in _REVALIDATION_HEADERS
    }


def _media_type_of(response: Response) -> str | None:
    """The response media type, falling back to a directly-set Content-Type."""
    if response.media_type is not None:
        return response.media_type
    return response.headers.get("content-type")


def _build_cache_control(
    *,
    ttl: int | None,
    stale: Literal["error", "revalidate"] | None,
    stale_ttl: int | None,
    no_cache: bool,
    public: bool,
    private: bool,
    immutable: bool,
    must_revalidate: bool,
) -> str:
    """The ``Cache-Control`` value for a ``@cache`` route's arguments.

    ``no_cache`` sends only ``no-cache`` (plus ``must-revalidate``); otherwise
    the directives follow in a fixed order: scope, ``max-age``,
    ``must-revalidate``, the stale directive, ``immutable``.
    """
    cache_control = CacheControl()
    if no_cache:
        cache_control.add(DirectiveType.NO_CACHE)
        if must_revalidate:
            cache_control.add(DirectiveType.MUST_REVALIDATE)
        return str(cache_control)

    # 1. Access scope (public/private)
    if public:
        cache_control.add(DirectiveType.PUBLIC)
    elif private:
        cache_control.add(DirectiveType.PRIVATE)

    # 2. Cache time settings
    if ttl is not None:
        cache_control.add(DirectiveType.MAX_AGE, ttl)

    # 3. Validation related
    if must_revalidate:
        cache_control.add(DirectiveType.MUST_REVALIDATE)

    # 4. Stale response handling (stale_ttl is validated at decoration time)
    if stale == "revalidate":
        cache_control.add(DirectiveType.STALE_WHILE_REVALIDATE, stale_ttl)
    elif stale == "error":
        cache_control.add(DirectiveType.STALE_IF_ERROR, stale_ttl)

    # 5. Special flags
    if immutable:
        cache_control.add(DirectiveType.IMMUTABLE)

    return str(cache_control)


def _is_request_annotation(annotation: Any) -> bool:
    """Whether an annotation asks for a ``Request`` (or a subclass of one)."""
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return isinstance(annotation, type) and issubclass(annotation, Request)


def _find_request_param(
    func: HandlerCallable, params: list[Parameter]
) -> Parameter | None:
    """The handler's own ``Request`` parameter, if it declares one.

    Annotations are resolved first, so a handler under ``from __future__ import
    annotations`` (where the annotation is the string ``"Request"``),
    ``Annotated[Request, ...]``, or a ``Request`` subclass is recognised
    instead of being given a second, unused request parameter. Resolution can
    fail on a forward reference that does not resolve in the handler's module,
    which must not break decoration: the raw annotations are used instead.
    """
    try:
        hints = get_type_hints(inspect.unwrap(func), include_extras=True)
    except Exception:  # noqa: BLE001 - any resolution failure falls back
        hints = {}

    return next(
        (
            param
            for param in params
            if _is_request_annotation(hints.get(param.name, param.annotation))
        ),
        None,
    )


def _get_response_body(response: Response) -> bytes | None:
    """Return response body bytes, or None for streaming/file responses."""
    return getattr(response, "body", None)


def _etag_for(body: bytes) -> str:
    return f'W/"{hashlib.md5(body).hexdigest()}"'  # noqa: S324


def _weak_etag(etag: str) -> str:
    """An ETag reduced to its opaque tag, so weak and strong forms compare equal."""
    tag = etag.strip()
    if tag[:2].upper() == "W/":
        tag = tag[2:]
    return tag.strip('"')


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` header selects ``etag`` (RFC 9110 §8.8.3.2).

    If-None-Match uses the weak comparison function, so the ``W/`` prefix is
    ignored on both sides, and it may list several validators. ``*`` matches
    whenever the resource exists, which every caller has already established
    before asking.

    Candidates are split on commas. That misreads an opaque-tag containing a
    literal comma, which this library never generates and RFC 9110 treats as
    pathological; the simpler split is worth the edge case.
    """
    if not if_none_match:
        return False

    header = if_none_match.strip()
    if header == "*":
        return True

    target = _weak_etag(etag)
    return any(_weak_etag(candidate) == target for candidate in header.split(","))


def _not_modified(
    etag: str, cache_control: str, headers: Mapping[str, str] | None = None
) -> Response:
    """Build the 304 for a successful revalidation.

    ``headers`` is what the 200 for this resource would have carried; RFC 9110
    §15.4.5 requires the fields that steer caching to be repeated on the 304,
    otherwise a cache that stored the 200 would drop them on refresh. ``Date``
    is added by Starlette and the other two are set here.
    """
    return Response(
        status_code=HTTP_304_NOT_MODIFIED,
        headers={
            **_revalidation_headers(headers),
            "ETag": etag,
            "Cache-Control": cache_control,
        },
    )


# Response directives by which the handler says its response belongs to one
# caller (``private``) or must not be kept at all (``no-store``).
_UNSHAREABLE_DIRECTIVES = frozenset(
    {DirectiveType.PRIVATE.value, DirectiveType.NO_STORE.value}
)


def _marked_unshareable(response: Response) -> bool:
    """Whether the handler's own ``Cache-Control`` has ``private`` or ``no-store``.

    Directive names are matched as whole tokens, case-insensitively, across
    every ``Cache-Control`` field the response carries.
    """
    return any(
        directive.split("=", 1)[0].strip().lower() in _UNSHAREABLE_DIRECTIVES
        for value in response.headers.getlist("cache-control")
        for directive in value.split(",")
    )


def _unshareable_reason(response: Response) -> str | None:
    """Why a rendered response must not be stored, or None when it may be."""
    if _marked_unshareable(response):
        return "response Cache-Control is private or no-store"
    if "set-cookie" in response.headers:
        return "response sets a cookie"
    return None


def _cache_control_for(
    response: Response, cache_control: str, private_cache_control: str
) -> str:
    """The ``Cache-Control`` to send for a response the handler just rendered.

    A handler that marked its response ``private`` or ``no-store`` keeps its own
    header; the decorator's would widen what the handler allowed. A response
    that sets a cookie gets ``private_cache_control``, so a shared cache in
    front of the app does not store it either.
    """
    if _marked_unshareable(response):
        return ", ".join(response.headers.getlist("cache-control"))
    if "set-cookie" in response.headers:
        return private_cache_control
    return cache_control


def _with_cache_control(
    response: Response, cache_control: str, private_cache_control: str
) -> Response:
    if not _marked_unshareable(response):
        response.headers["Cache-Control"] = _cache_control_for(
            response, cache_control, private_cache_control
        )
    return response


async def _render(
    func: HandlerCallable, request: Request, /, *args: Any, **kwargs: Any
) -> tuple[Response, bytes | None, str | None]:
    """Run the handler; the body and ETag are None for streaming/file responses."""
    response = await get_response(func, request, *args, **kwargs)
    body = _get_response_body(response)
    return response, body, None if body is None else _etag_for(body)


# Attribute a route's response-model TypeAdapter is kept under, so it is built
# once per route rather than on every cache miss.
_ADAPTER_ATTR = "_cachex_response_adapter"


def _serialize_result(route: "APIRoute", result: object) -> object:
    """JSON-compatible content for a handler's non-Response return value.

    With a response model (declared, or inferred from the return annotation)
    the result is validated against it and dumped with the route's
    ``response_model_*`` options, so fields the model leaves out are dropped.
    Otherwise it goes through ``jsonable_encoder``, as FastAPI does.
    """
    if route.response_model is None:
        return jsonable_encoder(result)
    adapter: TypeAdapter[Any] | None = getattr(route, _ADAPTER_ATTR, None)
    if adapter is None:
        adapter = TypeAdapter(route.response_model)
        setattr(route, _ADAPTER_ATTR, adapter)
    validated = adapter.validate_python(result, from_attributes=True)
    return adapter.dump_python(
        validated,
        mode="json",
        include=route.response_model_include,
        exclude=route.response_model_exclude,
        by_alias=route.response_model_by_alias,
        exclude_unset=route.response_model_exclude_unset,
        exclude_defaults=route.response_model_exclude_defaults,
        exclude_none=route.response_model_exclude_none,
    )


def _is_coroutine_callable(func: HandlerCallable) -> bool:
    """Report whether calling `func` returns a coroutine.

    `inspect.iscoroutinefunction` already sees through `functools.partial`;
    an instance with an ``async def __call__`` needs its method checked. The
    method is looked up on the type, as the call itself does, so a class
    (whose type is ``type``) counts as sync.
    """
    return inspect.iscoroutinefunction(func) or inspect.iscoroutinefunction(
        type(func).__call__
    )


async def get_response(
    __func: HandlerCallable,
    __request: Request,
    /,
    *args: Any,
    **kwargs: Any,
) -> Response:
    """Get the response from the function.

    Coroutine handlers are awaited. Sync handlers run in the threadpool, as
    FastAPI would run them without the (async) cache wrapper, so blocking I/O
    in a ``def`` handler does not stall the event loop.
    """
    if _is_coroutine_callable(__func):
        result = await cast("Callable[..., Awaitable[object]]", __func)(*args, **kwargs)
    else:
        result = await run_in_threadpool(__func, *args, **kwargs)
    # A sync callable can still hand back an awaitable (a lambda wrapping a
    # coroutine function, say); await it rather than try to encode it.
    if inspect.isawaitable(result):
        result = await result

    # If already a Response object, return it directly
    if isinstance(result, Response):
        return result

    # Get response_class from route if available
    route: APIRoute | None = __request.scope.get("route")
    if route is None:
        msg = "Route not found in request scope"
        raise CacheXError(msg)

    # A placeholder means this route uses the application default. Unwrap the
    # application value instead of calling the route's DefaultPlaceholder.
    route_response_class = route.response_class
    application_default_response_class = __request.app.router.default_response_class
    response_class: type[Response] = cast(
        "type[Response]",
        (
            getattr(
                application_default_response_class,
                "value",
                application_default_response_class,
            )
            if hasattr(route_response_class, "value")
            else route_response_class
        ),
    )

    # Build the response the way FastAPI would have without the cache wrapper:
    # serialize through the response model, apply the route's status code and
    # carry over what the handler set on an injected `response: Response`.
    sub_response = next(
        (value for value in kwargs.values() if isinstance(value, Response)), None
    )
    status_code = route.status_code
    if sub_response is not None and sub_response.status_code:
        status_code = sub_response.status_code
    response_args: dict[str, Any] = {}
    if status_code is not None:
        response_args["status_code"] = status_code

    response = response_class(_serialize_result(route, result), **response_args)
    if not is_body_allowed_for_status_code(response.status_code):
        response.body = b""
    if sub_response is not None:
        response.headers.raw.extend(sub_response.headers.raw)
    return response


def cache(
    ttl: int | None = None,
    stale_ttl: int | None = None,
    *,
    stale: Literal["error", "revalidate"] | None = None,
    no_cache: bool = False,
    no_store: bool = False,
    public: bool = False,
    private: bool = False,
    immutable: bool = False,
    must_revalidate: bool = False,
    key_builder: CacheKeyBuilder | None = None,
    fail_open: bool = True,
    cache_authorized: bool = False,
    vary: Sequence[str] | None = None,
) -> Callable[[HandlerCallable], AsyncResponseCallable]:
    """Cache decorator for FastAPI route handlers.

    Only GET requests go through the cache; other methods run the handler
    unchanged.

    A response is never stored when the handler marks it ``private`` or
    ``no-store`` in its own ``Cache-Control`` (that header is then sent
    unchanged instead of the decorator's) or when it sets a cookie. Such a
    response is served as rendered, and an entry already stored under its key
    is left alone. A response that sets a cookie is sent with ``private`` in
    place of ``public`` (the other directives stay), so that a shared cache in
    front of the app does not store it either.

    Args:
        ttl: How long, in seconds, a stored response may be served without
            running the handler. The same value is sent as ``max-age``.
            Without a positive ``ttl`` (``None``, or ``0``, which sends
            ``max-age=0``) nothing is read from or written to the backend: the
            handler runs on every request, and ``If-None-Match`` gets a 304
            only when it matches the freshly rendered response. Negative values
            are rejected.
        stale_ttl: Seconds sent with the directive chosen by ``stale``. It only
            shapes the ``Cache-Control`` header; the backend entry still
            expires after ``ttl``. Must be given together with ``stale``.
        stale: ``"revalidate"`` sends ``stale-while-revalidate=<stale_ttl>``,
            ``"error"`` sends ``stale-if-error=<stale_ttl>``.
        no_cache: Run the handler on every request and send ``no-cache``. The
            response is still stored when ``ttl`` is positive, and
            ``If-None-Match`` still gets a 304 when it matches the fresh ETag. The header then carries only
            ``no-cache`` (plus ``must-revalidate`` when set); ``ttl``,
            ``stale``, ``public``/``private`` and ``immutable`` are left out.
        no_store: Run the handler, store nothing, and send ``no-store``. Takes
            precedence over every other option.
        public: Send ``public``. Mutually exclusive with ``private``.
        private: Send ``private`` and bypass the shared backend entirely: the
            handler runs on every request and nothing is read or stored. ETag
            revalidation still works against the freshly rendered response.
            Mutually exclusive with ``public``.
        immutable: Send ``immutable``.
        must_revalidate: Send ``must-revalidate``.
        key_builder: Custom function to build cache keys. If None, uses
            ``default_key_builder``. To add a component (user ID, tenant,
            locale) to the default key, return
            ``build_cache_key(request, component)``.
        fail_open: When the backend raises, log a warning and answer without
            the cache: a failed read counts as a miss and a failed write
            leaves the response unstored. ``False`` lets the error propagate,
            so the request fails.
        cache_authorized: Read and write the backend for requests that carry
            an ``Authorization`` header. By default such a request bypasses
            the backend as ``private=True`` does (RFC 9111 §3.5), unless
            ``public`` is set, and its response is sent with ``private``.
            RFC 9111 would also allow reuse under ``must-revalidate``, but
            ``must_revalidate=True`` does not lift the bypass: only this
            explicit opt-in or ``public`` does. Set this only when
            ``key_builder`` puts the verified caller's identity into the key;
            with the default key builder one user's response would be served
            to the next.
        vary: Request header names the response depends on, e.g.
            ``["Accept-Language"]``. Each header's value (trimmed; empty when
            missing) is appended to the key, after whatever ``key_builder``
            returns, as a ``name=value`` component, so every distinct value
            gets its own entry. The names are also added to the ``Vary``
            header of every response to a GET request, unless it already
            lists them or ``*``. Values are client-controlled: each listed
            header multiplies the number of entries, so normalise them in a
            ``key_builder`` when only a few values matter.

    Returns:
        Decorator function that wraps route handlers with caching logic

    Raises:
        CacheXError: When the decorator is applied, if ``stale`` and
            ``stale_ttl`` are not given together, if ``public`` and
            ``private`` are both set, if ``ttl`` is not an ``int``, is
            negative or is larger than ``MAX_TTL``, or if ``vary`` is not a
            list of header field names (a single string is rejected).
    """

    def decorator(func: HandlerCallable) -> AsyncResponseCallable:
        # Validate parameters eagerly at decoration time
        if stale is not None and stale_ttl is None:
            msg = "stale_ttl must be set if stale is used"
            raise CacheXError(msg)
        if stale_ttl is not None and stale is None:
            msg = "stale must be set if stale_ttl is used"
            raise CacheXError(msg)
        if public and private:
            msg = "public and private are mutually exclusive"
            raise CacheXError(msg)
        if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int)):
            # Checked here: at request time the backend would reject it, and
            # failing open would hide that the route never caches.
            msg = f"ttl must be an int number of seconds, got {type(ttl).__name__}"
            raise CacheXError(msg)
        if ttl is not None and ttl < 0:
            msg = "ttl must not be negative"
            raise CacheXError(msg)
        if ttl is not None and ttl > MAX_TTL:
            msg = f"ttl must be at most {MAX_TTL} seconds"
            raise CacheXError(msg)
        vary_names = _validate_vary(vary)

        # Analyze the original function's signature
        sig: Signature = inspect.signature(func)
        params: list[Parameter] = list(sig.parameters.values())

        # Check if Request is already in the parameters
        found_request: Parameter | None = _find_request_param(func, params)

        # Add Request parameter if it's not present
        if not found_request:
            request_name: str = "__cachex_request"

            request_param = inspect.Parameter(
                request_name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Request,
            )

            # A keyword-only parameter must precede **kwargs; appending it
            # after one makes `Signature.replace` raise at decoration time,
            # so a handler taking **kwargs could not be cached at all.
            insert_at = next(
                (
                    index
                    for index, param in enumerate(params)
                    if param.kind is Parameter.VAR_KEYWORD
                ),
                len(params),
            )
            sig = sig.replace(
                parameters=[
                    *params[:insert_at],
                    request_param,
                    *params[insert_at:],
                ]
            )

        else:
            request_name = found_request.name

        # The header only depends on the decorator arguments, so build it once.
        cache_control = _build_cache_control(
            ttl=ttl,
            stale=stale,
            stale_ttl=stale_ttl,
            no_cache=no_cache,
            public=public,
            private=private,
            immutable=immutable,
            must_revalidate=must_revalidate,
        )
        # Sent instead for a response that must not be stored downstream
        # either: one that sets a cookie, or one answering an `Authorization`
        # request the backend was bypassed for. `public` becomes `private`;
        # `no_cache` leaves the scope out of `cache_control`, so it is added.
        private_cache_control = (
            f"{DirectiveType.PRIVATE.value}, {cache_control}"
            if no_cache
            else _build_cache_control(
                ttl=ttl,
                stale=stale,
                stale_ttl=stale_ttl,
                no_cache=no_cache,
                public=False,
                private=True,
                immutable=immutable,
                must_revalidate=must_revalidate,
            )
        )
        builder = key_builder or default_key_builder
        # Without a positive ttl nothing may be served from storage, and a 304
        # answered from a stored ETag would be exactly that: it would keep
        # confirming a copy that the handler no longer produces (#110). Such
        # routes skip the backend like private ones. `ttl=0` is included, since
        # `max-age=0` allows no reuse either.
        bypass_backend = private or not ttl

        @wraps(func)
        async def serve(*args: Any, **kwargs: Any) -> Response:
            # Resolve backend on every request to support lifespan-configured backends
            cache_backend = get_backend_or_fallback()

            if found_request:
                req: Request | None = kwargs.get(request_name)
            else:
                req = kwargs.pop(request_name, None)

            if req is None:
                # Reached when the wrapper is called outside the router, which
                # is the only caller that supplies the request parameter.
                raise RequestNotFoundError

            # Only cache GET requests
            if req.method != "GET":
                logger.debug(
                    "Non-GET request; bypassing cache for method=%s", req.method
                )
                return await get_response(func, req, *args, **kwargs)

            # Handle special case: no-store (highest priority)
            if no_store:
                response = await get_response(func, req, *args, **kwargs)
                logger.debug(
                    "no-store active; bypassed cache for path=%s", req.url.path
                )
                # Unconditional: `no-store` is stricter than anything the
                # handler may have sent.
                response.headers["Cache-Control"] = _NO_STORE
                return response

            client_etag = req.headers.get("if-none-match")

            # RFC 9111 §3.5: a shared cache must not reuse a response to a
            # request with `Authorization` unless the response allows it. The
            # default key carries no identity, so treat such requests as
            # private unless the route is `public` or opted in.
            authorized_bypass = (
                not bypass_backend
                and not public
                and not cache_authorized
                and "authorization" in req.headers
            )
            if authorized_bypass:
                logger.debug(
                    "Authorization header present; bypassing the backend for path=%s",
                    req.url.path,
                )

            # A private response belongs to exactly one user, so it must never
            # be read from or written to the shared backend — the default cache
            # key carries no identity, so a stored copy would be served to the
            # next caller. The same path serves routes without a positive ttl
            # (see `bypass_backend`). ETag revalidation still works: it
            # compares the client's validator against freshly rendered content.
            if bypass_backend or authorized_bypass:
                # Without `public`/`cache_authorized`, RFC 9111 §3.5 would still
                # let a downstream shared cache reuse the answer to an
                # `Authorization` request under `must-revalidate`; `private`
                # rules that out.
                bypass_cache_control = (
                    private_cache_control if authorized_bypass else cache_control
                )
                response, _, etag = await _render(func, req, *args, **kwargs)
                if not _is_cacheable_status(response.status_code):
                    return response
                if etag is None:
                    # StreamingResponse/FileResponse — cannot compute ETag
                    return _with_cache_control(
                        response, bypass_cache_control, private_cache_control
                    )
                if _etag_matches(client_etag, etag):
                    logger.debug("304 Not Modified (uncached); path=%s", req.url.path)
                    return _not_modified(
                        etag,
                        _cache_control_for(
                            response, bypass_cache_control, private_cache_control
                        ),
                        response.headers,
                    )
                response.headers["ETag"] = etag
                logger.debug("Bypassed the backend; path=%s", req.url.path)
                return _with_cache_control(
                    response, bypass_cache_control, private_cache_control
                )

            # Built only here: the branches above never touch the backend, so a
            # custom key builder would run for nothing.
            cache_key = _append_key_components(
                builder(req), _vary_components(req, vary_names)
            )

            try:
                cached_data = await cache_backend.get(cache_key)
            except Exception as e:
                if not fail_open:
                    raise
                logger.warning(
                    "Cache backend read failed; serving uncached. key=%s error=%r",
                    cache_key,
                    e,
                )
                cached_data = None

            current_response: Response | None = None
            current_body: bytes | None = None
            current_etag: str | None = None

            if client_etag:
                if no_cache:
                    # Get fresh response first if using no-cache
                    current_response, current_body, current_etag = await _render(
                        func, req, *args, **kwargs
                    )
                    if not _is_cacheable_status(current_response.status_code):
                        # Error responses carry no validator: never answer 304.
                        logger.debug(
                            "Uncacheable status %s; serving as-is for key=%s",
                            current_response.status_code,
                            cache_key,
                        )
                        return current_response

                    if current_etag is None:
                        # StreamingResponse/FileResponse — cannot compute ETag; serve as-is
                        return _with_cache_control(
                            current_response, cache_control, private_cache_control
                        )

                    if _etag_matches(client_etag, current_etag):
                        # For no-cache, compare fresh data with client's ETag
                        logger.debug("304 Not Modified via no-cache; key=%s", cache_key)
                        return _not_modified(
                            current_etag,
                            _cache_control_for(
                                current_response, cache_control, private_cache_control
                            ),
                            current_response.headers,
                        )

                # Compare with cached ETag - if match, return 304
                elif cached_data and _etag_matches(
                    client_etag, cached_data.fingerprint
                ):
                    logger.debug(
                        "304 Not Modified (cached ETag match); key=%s", cache_key
                    )
                    return _not_modified(
                        cached_data.fingerprint, cache_control, cached_data.headers
                    )

            # If we don't have If-None-Match header, check if we have a valid cached copy
            # and can serve it directly (cache hit without ETag comparison)
            if cached_data and not no_cache:
                logger.debug("Cache HIT (TTL valid); key=%s", cache_key)
                return Response(
                    content=cached_data.content,
                    status_code=cached_data.status_code,
                    media_type=cached_data.media_type,
                    headers={
                        **(cached_data.headers or {}),
                        "ETag": cached_data.fingerprint,
                        "Cache-Control": cache_control,
                    },
                )

            if current_response is None or current_etag is None:
                # Retrieve the current response if not already done
                current_response, current_body, current_etag = await _render(
                    func, req, *args, **kwargs
                )
                if not _is_cacheable_status(current_response.status_code):
                    # Leave any existing entry alone: a transient error must not
                    # evict or overwrite the last good response.
                    logger.debug(
                        "Uncacheable status %s; not storing key=%s",
                        current_response.status_code,
                        cache_key,
                    )
                    return current_response

                if current_etag is None:
                    # StreamingResponse/FileResponse — cannot compute ETag; serve as-is
                    return _with_cache_control(
                        current_response, cache_control, private_cache_control
                    )
                logger.debug("Cache MISS; computed fresh ETag for key=%s", cache_key)

            current_response.headers["ETag"] = current_etag

            # A response the handler scoped to one caller is served but never
            # stored. An entry already under this key is left alone, as for an
            # error status: it came from a response that was shareable.
            skip_reason = _unshareable_reason(current_response)
            if skip_reason is not None:
                logger.debug("Not storing key=%s: %s", cache_key, skip_reason)

            # Update cache if needed
            elif not cached_data or cached_data.fingerprint != current_etag:
                assert (
                    current_body is not None
                )  # guaranteed by early-return guards above
                try:
                    await cache_backend.set(
                        cache_key,
                        CacheEntry(
                            fingerprint=current_etag,
                            content=current_body,
                            media_type=_media_type_of(current_response),
                            status_code=current_response.status_code,
                            headers=_cacheable_headers(current_response),
                        ),
                        ttl=ttl,
                    )
                except Exception as e:
                    if not fail_open:
                        raise
                    logger.warning(
                        "Cache backend write failed; response not stored. key=%s error=%r",
                        cache_key,
                        e,
                    )
                else:
                    logger.debug("Updated cache entry; key=%s ttl=%s", cache_key, ttl)

            return _with_cache_control(
                current_response, cache_control, private_cache_control
            )

        wrapper: AsyncResponseCallable = serve
        if vary_names:

            @wraps(func)
            async def with_vary(*args: Any, **kwargs: Any) -> Response:
                # Read before `serve` pops an injected request parameter.
                req: Request | None = kwargs.get(request_name)
                response = await serve(*args, **kwargs)
                if req is not None and req.method == "GET":
                    # Every GET answer, served from the backend or not: a
                    # shared cache downstream keys on these headers too.
                    add_vary(response.headers, vary_names)
                return response

            wrapper = with_vary

        # Update the wrapper with the new signature
        update_wrapper(wrapper, func)
        wrapper.__signature__ = sig  # type: ignore[attr-defined]

        return wrapper

    return decorator
