"""Whether a pydantic model's type rules out a secret in its values (#668).

``@cached`` builds a model argument's key with the model's own serializer,
skipping the search for a secret, when its core schema can hold no value but
plain data (`_plain_model`). That rests on pydantic, under
``warnings="error"``, never writing a secret held where the declared type
does not allow one (put in by ``model_construct()``, assignment or a
validator): it raises, or writes nothing of it (a model field writes only
its declared fields, so a secret there becomes ``{}``, as on the search
path). The behaviour differs between pydantic versions, so
`_verified_types` tries it on the installed one, once, and leaves out each
schema type that writes a misfit secret's mask without an error.
"""

import enum
import functools
import json
import uuid
from collections.abc import Callable
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from pydantic import SecretStr
from pydantic import computed_field
from pydantic.dataclasses import dataclass
from pydantic_core import SchemaSerializer
from pydantic_core import core_schema

# The keys holding child schemas, per core schema type whose own values
# cannot be a secret. Anything else is not plain: `any`, `is-instance` (a
# secret's own schema), plain or wrap validators, and types added later.
# Validators before or after a listed schema do not matter: a value they
# return that does not fit its type fails the `warnings="error"` dump. That
# is checked for the installed pydantic by `_verified_types`. Not `literal`,
# nor an enum without a str, int or float mixin: their serializers write a
# value that does not fit without a warning.
_CHILD_KEYS: dict[str, tuple[str, ...]] = {
    "model": ("schema",),
    "model-fields": ("fields", "computed_fields"),
    "model-field": ("schema",),
    "computed-field": ("return_schema",),
    "dataclass": ("schema",),
    "dataclass-args": ("fields", "computed_fields"),
    "dataclass-field": ("schema",),
    "typed-dict": ("fields", "computed_fields"),
    "typed-dict-field": ("schema",),
    "list": ("items_schema",),
    "set": ("items_schema",),
    "frozenset": ("items_schema",),
    "tuple": ("items_schema",),
    "dict": ("keys_schema", "values_schema"),
    "nullable": ("schema",),
    "default": ("schema",),
    "definitions": ("schema", "definitions"),
    "definition-ref": (),
    "union": ("choices",),
    "tagged-union": ("choices",),
    "chain": ("steps",),
    "lax-or-strict": ("lax_schema", "strict_schema"),
    "json-or-python": ("json_schema", "python_schema"),
    "function-before": ("schema",),
    "function-after": ("schema",),
    **dict.fromkeys(
        (
            "none", "bool", "int", "float", "decimal", "complex", "str",
            "bytes", "date", "time", "datetime", "timedelta", "uuid", "url",
            "multi-host-url",
        ),
        (),
    ),
}  # fmt: skip
_EXCLUDABLE = frozenset({"model-field", "dataclass-field", "typed-dict-field"})


def _child_schemas(schema: dict[str, Any]) -> list[Any] | None:
    """A core schema's child schemas, or None for a type not known to be plain."""
    kind = schema["type"]
    if kind == "enum":
        kind = f"enum:{schema.get('sub_type')}"
    if kind not in _verified_types():
        return None
    keys = _CHILD_KEYS.get(kind, ())
    children: list[Any] = []
    for key in keys:
        child = schema.get(key)
        if child is None:
            continue
        if isinstance(child, dict) and key in ("fields", "choices"):
            children.extend(child.values())  # keyed by field name or tag
        elif isinstance(child, list):
            # A union choice may be a (schema, tag) pair.
            children.extend(c[0] if isinstance(c, tuple) else c for c in child)
        else:
            children.append(child)
    return children


def _node_may_hold_a_secret(schema: dict[str, Any]) -> bool:
    """Whether a core schema node, apart from its children, can hold a secret.

    True for a serializer of its own (it may write anything, a secret's mask
    included) and extra fields kept with ``extra="allow"``.
    """
    return (
        schema.get("serialization") is not None
        or schema.get("extra_behavior") == "allow"
        or schema.get("config", {}).get("extra_fields_behavior") == "allow"
    )


def _schema_is_plain(schema: object) -> bool:
    """Whether a core schema can hold no value but plain data; fails closed."""
    if not isinstance(schema, dict) or not isinstance(schema.get("type"), str):
        return False
    if schema["type"] in _EXCLUDABLE and schema.get("serialization_exclude"):
        return True  # not written out, so not in the key
    children = _child_schemas(schema)
    return (
        children is not None
        and not _node_may_hold_a_secret(schema)
        and all(map(_schema_is_plain, children))
    )


@functools.lru_cache(maxsize=256)
def _built_model_is_plain(
    kind: type[BaseModel],
    schema_id: int,  # noqa: ARG001 - in the cache key only
) -> tuple[Any, bool]:
    """The model's schema and whether it is plain.

    Keyed by the schema's id too, so ``model_rebuild(force=True)`` gets a
    new answer; the schema kept in the result stops its id being reused.
    """
    schema = kind.__dict__["__pydantic_core_schema__"]
    try:
        return schema, _schema_is_plain(schema)
    except Exception:  # noqa: BLE001 - an unusual schema: search instead
        return schema, False


def _plain_model(kind: type[BaseModel]) -> bool:
    """Whether instances of a pydantic model can hold no secret (#668).

    A model not fully built yet (a forward reference, ``defer_build``) is not
    plain, and that answer is not cached. Its schema is a mock, or on older
    pydantic it has none of its own and would read its parent's.
    """
    schema = kind.__dict__.get("__pydantic_core_schema__")
    if getattr(kind, "__pydantic_complete__", False) is not True or not isinstance(
        schema, dict
    ):
        return False
    cached, plain = _built_model_is_plain(kind, id(schema))
    # Not the same object only if a rebuild ran between the two reads.
    return plain and cached is schema


_MIXINS = {"enum:str": str, "enum:int": int, "enum:float": float}


# A secret that is also an instance of the type's own class passes its
# isinstance check; some serializers then write its `str()`, the mask. Each
# entry gives the class and the arguments to make an instance.
_HYBRIDS: dict[str, tuple[type[Any], tuple[Any, ...]]] = {
    "str": (str, ("p",)),
    "int": (int, (3,)),
    "float": (float, (1.5,)),
    "complex": (complex, (1,)),
    "bytes": (bytes, (b"p",)),
    "decimal": (Decimal, ("1.5",)),
    "date": (date, (2020, 1, 1)),
    "datetime": (datetime, (2020, 1, 1)),
    "time": (time, (1,)),
    "timedelta": (timedelta, (1,)),
    "uuid": (uuid.UUID, ()),  # set up by __init__ below
}


def _hybrid(kind: str) -> Any:
    """A ``SecretStr`` that is also an instance of a plain type, or None."""
    base, args = _HYBRIDS[kind]
    try:
        value = base.__new__(type("Probe", (SecretStr, base), {}), *args)
        if base is uuid.UUID:
            uuid.UUID.__init__(value, int=5)
        object.__setattr__(value, "_secret_value", "probe")
    except Exception:  # noqa: BLE001 - the two cannot be combined
        return None
    return value


_Probes = dict[str, list[tuple[Callable[[], Any], Any]]]
_SECRET = SecretStr("probe")
_INT = core_schema.int_schema()


def _core(schema: Any) -> Callable[[], Any]:
    """Builds a serializer for a core schema when called."""
    return lambda: SchemaSerializer(schema)


def _own(cls: type[Any]) -> Callable[[], Any]:
    """Gives a model's or dataclass's own serializer when called."""
    return lambda: cls.__pydantic_serializer__


def _leaf_probes() -> _Probes:
    """Leaf types and mixin enums, given a secret, or a secret of their class."""
    found: _Probes = {
        kind: [(_core({"type": kind}), _SECRET)]
        for kind, keys in _CHILD_KEYS.items()
        if not keys
    }
    for kind in _HYBRIDS:
        hybrid = _hybrid(kind)
        if hybrid is not None:  # a type that cannot be mixed into a secret
            found[kind].append((_core({"type": kind}), hybrid))
    for kind, mixin in _MIXINS.items():
        members = enum.Enum("Probe", {"A": mixin(1)}, type=mixin)  # type: ignore[misc]
        schema = core_schema.enum_schema(  # type: ignore[call-overload]
            members, list(members), sub_type=kind.removeprefix("enum:")
        )
        found[kind] = [(_core(schema), _SECRET)]
    return found


def _class_probes() -> _Probes:
    """Models, dataclasses and typed dicts, holding a secret in an int field."""

    class Model(BaseModel):
        x: int

    class Outer(BaseModel):
        inner: Model

    class Computed(BaseModel):
        @computed_field  # type: ignore[prop-decorator]
        @property
        def x(self) -> int:
            return _SECRET  # type: ignore[return-value]

    @dataclass
    class Data:
        x: int

    data = Data(x=1)
    data.x = _SECRET  # type: ignore[assignment]
    typed_dict = core_schema.typed_dict_schema(
        {"x": core_schema.typed_dict_field(_INT)}
    )
    models = [
        (_own(Model), Model.model_construct(x=_SECRET)),  # type: ignore[arg-type]
        (_own(Outer), Outer.model_construct(inner=_SECRET)),  # type: ignore[arg-type]
    ]
    return {
        "model": models,
        "model-fields": models,
        "model-field": models,
        "computed-field": [(_own(Computed), Computed())],
        **{
            kind: [(_own(Data), data)]
            for kind in ("dataclass", "dataclass-args", "dataclass-field")
        },
        **{
            kind: [(_core(typed_dict), {"x": _SECRET}), (_core(typed_dict), _SECRET)]
            for kind in ("typed-dict", "typed-dict-field")
        },
        "tagged-union": [
            (
                _core(
                    core_schema.tagged_union_schema(
                        {"a": typed_dict}, discriminator="x"
                    )
                ),
                _SECRET,
            )
        ],
    }


def _container_probes() -> _Probes:
    """Containers and wrappers of an int schema, holding a secret."""
    cs = core_schema
    secret = _SECRET
    tuple_ = cs.tuple_schema([_INT])
    dict_ = cs.dict_schema(_INT, _INT)
    refs = cs.definitions_schema(
        cs.definition_reference_schema("p"), [cs.int_schema(ref="p")]
    )
    found: _Probes = {
        kind: [(_core(build(_INT)), wrap([secret])), (_core(build(_INT)), secret)]
        for kind, build, wrap in (
            ("list", cs.list_schema, list),
            ("set", cs.set_schema, set),
            ("frozenset", cs.frozenset_schema, frozenset),
        )
    }
    found.update(
        {
            "tuple": [
                (_core(tuple_), (secret,)),
                (_core(cs.tuple_schema([_INT], variadic_item_index=0)), (1, secret)),
                (_core(tuple_), secret),
            ],
            "dict": [
                (_core(dict_), {secret: 1}),
                (_core(dict_), {1: secret}),
                (_core(dict_), secret),
            ],
            "nullable": [(_core(cs.nullable_schema(_INT)), secret)],
            "default": [(_core(cs.with_default_schema(_INT, default=1)), secret)],
            "definitions": [(_core(refs), secret)],
            "definition-ref": [(_core(refs), secret)],
            "union": [
                (_core(cs.union_schema([_INT, cs.str_schema()])), secret),
                (_core(cs.union_schema([(_INT, "i"), (cs.str_schema(), "s")])), secret),
            ],
            "chain": [(_core(cs.chain_schema([cs.str_schema(), _INT])), secret)],
            "lax-or-strict": [(_core(cs.lax_or_strict_schema(_INT, _INT)), secret)],
            "json-or-python": [(_core(cs.json_or_python_schema(_INT, _INT)), secret)],
            "function-before": [
                (_core(cs.no_info_before_validator_function(lambda v: v, _INT)), secret)
            ],
            "function-after": [
                (_core(cs.no_info_after_validator_function(lambda v: v, _INT)), secret)
            ],
        }
    )
    return found


def _probes() -> _Probes:
    """Per schema type, (serializer factory, misfit value) pairs to try.

    Each value puts a secret where the type does not allow one, at the node
    itself or through its children.
    """
    return {**_leaf_probes(), **_class_probes(), **_container_probes()}


def _writes_the_mask(make: Callable[[], Any], value: Any) -> bool:
    """Whether a serializer writes ``value``'s secret without an error.

    One that cannot be built (a type this pydantic lacks) counts too.
    """
    try:
        serializer = make()
    except Exception:  # noqa: BLE001
        return True
    try:
        out = serializer.to_python(value, mode="json", warnings="error")
    except Exception:  # noqa: BLE001 - what the check relies on
        return False
    return "**********" in json.dumps(out, default=str)


@functools.cache
def _verified_types() -> frozenset[str]:
    """The schema types that refuse a misfit secret on this pydantic."""
    return frozenset(
        kind
        for kind, tries in _probes().items()
        if not any(_writes_the_mask(make, value) for make, value in tries)
    )
