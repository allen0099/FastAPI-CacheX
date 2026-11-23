"""``@cached`` caches a plain function's result through ``CacheManager`` (#248)."""

import asyncio
import collections
import dataclasses
import enum
import functools
import hashlib
import json
import os
import subprocess
import sys
import typing
from collections.abc import AsyncIterator
from collections.abc import Callable
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Any
from typing import Optional
from uuid import UUID

import pytest
import pytest_asyncio
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import Secret
from pydantic import SecretBytes
from pydantic import SecretStr
from pydantic import computed_field
from pydantic import field_serializer
from pydantic import field_validator
from starlette.responses import Response

from fastapi_cachex import BackendProxy
from fastapi_cachex import CachedFunction
from fastapi_cachex import CacheManager
from fastapi_cachex import CacheManagerProxy
from fastapi_cachex import cached
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import CacheXError
from tests.conftest import Clock


@pytest_asyncio.fixture
async def manager(memory_backend: MemoryBackend) -> AsyncIterator[CacheManager]:
    BackendProxy.set(memory_backend)
    CacheManagerProxy.set(None)
    yield CacheManager(backend=memory_backend, key_prefix="t:")
    CacheManagerProxy.set(None)


def _digest(**arguments: Any) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def test_an_async_function_runs_once_per_arguments(
    manager: CacheManager,
) -> None:
    calls: list[int] = []

    @cached(ttl=60, manager=manager)
    async def load(user_id: int) -> dict[str, int]:
        calls.append(user_id)
        return {"id": user_id}

    assert await load(1) == {"id": 1}
    assert await load(1) == {"id": 1}
    assert await load(2) == {"id": 2}
    assert calls == [1, 2]


async def test_a_sync_function_is_awaited_too(manager: CacheManager) -> None:
    calls: list[int] = []

    @cached(ttl=60, manager=manager)
    def square(n: int) -> int:
        calls.append(n)
        return n * n

    assert await square(3) == 9
    assert await square(3) == 9
    assert calls == [3]


async def test_the_default_key_hashes_the_bound_arguments(
    manager: CacheManager,
) -> None:
    @cached(ttl=60, manager=manager)
    async def load(user_id: int, *, locale: str = "en") -> str:
        return f"{user_id}:{locale}"

    expected = f"{__name__}.{load.__qualname__}:{_digest(user_id=1, locale='en')}"
    assert load.cache_key(1) == expected
    # By position or by name, defaults applied: the same key.
    assert load.cache_key(user_id=1) == expected
    assert load.cache_key(1, locale="en") == expected
    assert load.cache_key(1, locale="fr") != expected

    await load(1)
    assert await manager.has(expected)
    assert await manager.get(expected) == "1:en"


async def test_a_non_json_argument_needs_a_key(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager)
    async def load(when: object) -> int:
        return 1

    with pytest.raises(CacheXError, match=r"not JSON-serializable.*Pass key="):
        load.cache_key(object())
    with pytest.raises(CacheXError, match="not JSON-serializable"):
        await load(object())


class _Creds(BaseModel):
    token: SecretStr


@dataclasses.dataclass
class _Client:
    tenant: str
    key: SecretBytes


class _Tier(enum.Enum):
    GOLD = SecretStr("tenant-a")


class _Plan(enum.Enum):
    PRO = _Creds(token=SecretStr("tenant-a"))


class _Pin(BaseModel):
    pin: Secret[int]


class _ByKey(BaseModel):
    tokens: dict[SecretStr, int]


class _NoSchema:
    """A type pydantic cannot build a schema for."""


@dataclasses.dataclass
class _Odd:
    extra: _NoSchema
    token: SecretStr


@pytest.mark.parametrize(
    "secret",
    [
        SecretStr("tenant-a"),
        _Creds(token=SecretStr("tenant-a")),
        {"auth": [SecretStr("tenant-a")]},
        _Client(tenant="a", key=SecretBytes(b"tenant-a")),
        frozenset({SecretStr("tenant-a")}),
        _Pin(pin=Secret[int](1234)),
        _ByKey(tokens={SecretStr("tenant-a"): 1}),
        _Odd(extra=1, token=SecretStr("tenant-a")),  # type: ignore[arg-type]
        collections.deque([SecretStr("tenant-a")]),
        _Tier.GOLD,
        {"plan": _Plan.PRO},
    ],
    ids=[
        "bare",
        "model",
        "nested",
        "dataclass",
        "set",
        "generic",
        "dict-key",
        "odd",
        "deque",
        "enum",
        "enum-of-model",
    ],
)
async def test_an_argument_holding_a_secret_needs_a_key(
    manager: CacheManager, secret: object
) -> None:
    """#654: pydantic writes a secret as its mask, so tenants shared a key."""
    calls = []

    @cached(ttl=60, manager=manager)
    async def load(creds: object) -> int:
        calls.append(creds)
        return len(calls)

    with pytest.raises(CacheXError, match=r"pydantic secret.*Pass key="):
        load.cache_key(secret)
    with pytest.raises(CacheXError, match="pydantic secret"):
        await load(secret)
    assert calls == []


async def test_a_secret_argument_with_a_key_is_cached_under_it(
    manager: CacheManager,
) -> None:
    @cached(ttl=60, manager=manager, key=lambda creds: creds.token.get_secret_value())
    async def load(creds: _Creds) -> str:
        return creds.token.get_secret_value()

    assert await load(_Creds(token=SecretStr("a"))) == "a"
    assert await load(_Creds(token=SecretStr("b"))) == "b"


class _Unmasked(BaseModel):
    token: SecretStr

    @field_serializer("token", when_used="json")
    def _reveal(self, token: SecretStr) -> str:
        return token.get_secret_value()


class _SecretDate(Secret[date]):
    """A secret with its own display, as pydantic's docs show."""

    def _display(self) -> str:
        return "****/**/**"


class _Embedded(BaseModel):
    token: SecretStr

    @field_serializer("token", when_used="json")
    def _prefix(self, token: SecretStr) -> str:
        return f"tok={token}"


@pytest.mark.parametrize(
    "secret",
    [
        _Unmasked(token=SecretStr("a")),
        _SecretDate(date(2020, 1, 1)),
        _Embedded(token=SecretStr("a")),
    ],
    ids=["serializer-writes-it-out", "own-display", "mask-in-a-longer-string"],
)
async def test_a_secret_not_written_as_the_bare_mask_is_refused_too(
    manager: CacheManager, secret: object
) -> None:
    """The check finds the secret objects, not only the mask in the output."""

    @cached(ttl=60, manager=manager)
    async def load(creds: object) -> int:
        return 1

    with pytest.raises(CacheXError, match="pydantic secret"):
        load.cache_key(secret)


@dataclasses.dataclass
class _Plain:
    extra: _NoSchema
    count: int


@dataclasses.dataclass
class _Count:
    count: int


async def test_a_dataclass_without_a_secret_is_keyed_as_before(
    manager: CacheManager,
) -> None:
    """Looking for a secret neither raises nor warns where keying did not."""

    @cached(ttl=60, manager=manager)
    async def load(item: object) -> int:
        return 1

    # pydantic cannot build a schema for `_Plain`, but can encode its values.
    first = load.cache_key(_Plain(extra=1, count=1))  # type: ignore[arg-type]
    # A value that does not match its annotation.
    second = load.cache_key(_Plain(extra=1, count="one"))  # type: ignore[arg-type]

    assert first != second
    # pydantic warns on dumping it; filterwarnings = error turns that into a failure.
    assert load.cache_key(_Count(count="one")) != load.cache_key(_Count(count=1))  # type: ignore[arg-type]


@dataclasses.dataclass
class _Node:
    extra: object
    nxt: object = None


@pytest.mark.parametrize("extra", [1, _NoSchema()], ids=["schema", "no-schema"])
async def test_a_cyclic_argument_needs_a_key(
    manager: CacheManager, extra: object
) -> None:
    """Looking for a secret stops on a cycle instead of recursing forever."""

    @cached(ttl=60, manager=manager)
    async def load(node: object) -> int:
        return 1

    node = _Node(extra=extra)
    node.nxt = node
    with pytest.raises(CacheXError, match="Pass key="):
        load.cache_key(node)


async def test_arguments_must_fit_the_signature(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager)
    async def load(user_id: int) -> int:
        return user_id

    with pytest.raises(TypeError):
        await load(1, 2)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        load.cache_key(other=1)  # type: ignore[call-arg]


async def test_a_str_key_is_a_template_over_the_arguments(
    manager: CacheManager,
) -> None:
    @cached(ttl=60, manager=manager, key="user:{user_id}:{locale}")
    async def load(user_id: int, locale: str = "en") -> str:
        return f"{user_id}:{locale}"

    assert load.cache_key(42) == "user:42:en"
    assert load.cache_key(42, locale="fr") == "user:42:fr"
    await load(42)
    assert await manager.get("user:42:en") == "42:en"


async def test_a_plain_str_key_is_fixed(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager, key="rates")
    async def rates() -> dict[str, float]:
        return {"EUR": 0.9}

    assert rates.cache_key() == "rates"
    assert await rates() == {"EUR": 0.9}
    assert await manager.get("rates") == {"EUR": 0.9}


async def test_a_callable_key_gets_the_arguments(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager, key=lambda item, *, verbose=False: f"i:{item}")
    async def load(item: int, *, verbose: bool = False) -> int:
        return item

    assert load.cache_key(7, verbose=True) == "i:7"
    assert await load(7) == 7
    assert await manager.get("i:7") == 7


async def test_a_callable_key_must_return_a_str(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager, key=lambda item: item)
    async def load(item: int) -> int:
        return item

    with pytest.raises(CacheXError, match="key must return a str, got int"):
        await load(7)


async def test_invalidate_drops_the_value_for_those_arguments(
    manager: CacheManager,
) -> None:
    calls: list[int] = []

    @cached(ttl=60, manager=manager)
    async def load(user_id: int) -> int:
        calls.append(user_id)
        return user_id

    await load(1)
    await load(2)
    assert await load.invalidate(1) is True
    assert await load.invalidate(1) is False
    await load(1)
    await load(2)
    assert calls == [1, 2, 1]


async def test_ttl_expires_the_value(manager: CacheManager, clock: Clock) -> None:
    calls: list[int] = []

    @cached(ttl=timedelta(minutes=1), manager=manager)
    async def load(n: int) -> int:
        calls.append(n)
        return n

    await load(1)
    clock.advance(59)
    await load(1)
    clock.advance(2)
    await load(1)
    assert calls == [1, 1]


async def test_without_ttl_the_managers_default_applies(
    memory_backend: MemoryBackend, clock: Clock
) -> None:
    manager = CacheManager(backend=memory_backend, default_ttl=10)
    calls: list[int] = []

    @cached(manager=manager)
    async def load(n: int) -> int:
        calls.append(n)
        return n

    await load(1)
    clock.advance(11)
    await load(1)
    assert calls == [1, 1]


async def test_the_value_comes_back_as_json_gives_it(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager, type_=None)
    async def pair() -> tuple[int, int]:
        return (1, 2)

    assert await pair() == [1, 2]  # type: ignore[comparison-overlap]
    assert await pair() == [1, 2]  # type: ignore[comparison-overlap]


async def test_a_non_json_result_is_not_stored(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager)
    async def when() -> object:
        return object()

    with pytest.raises(TypeError):
        await when()
    assert await manager.has(when.cache_key()) is False


async def test_without_a_manager_the_app_cache_is_used(
    memory_backend: MemoryBackend,
) -> None:
    BackendProxy.set(memory_backend)
    CacheManagerProxy.set(None)

    @cached(ttl=60)
    async def load(n: int) -> int:
        return n

    # Registered later, at startup say, and still picked up: the manager is
    # resolved on each call.
    app_manager = CacheManager(backend=memory_backend, key_prefix="app:")
    CacheManagerProxy.set(app_manager)
    try:
        assert load.manager is app_manager
        await load(1)
        assert await app_manager.has(load.cache_key(1))
    finally:
        CacheManagerProxy.set(None)


async def test_concurrent_misses_run_the_function_once(
    manager: CacheManager,
) -> None:
    calls: list[int] = []
    started = asyncio.Event()

    @cached(ttl=60, manager=manager)
    async def load(n: int) -> int:
        calls.append(n)
        started.set()
        await asyncio.sleep(0.05)
        return n

    results = await asyncio.gather(*(load(1) for _ in range(5)))
    assert results == [1] * 5
    assert calls == [1]


async def test_lock_false_skips_the_lock(memory_backend: MemoryBackend) -> None:
    manager = CacheManager(backend=memory_backend, key_prefix="t:")

    @cached(ttl=60, manager=manager, lock=False)
    async def load(n: int) -> int:
        assert await memory_backend.get("lock:" + "t:" + load.cache_key(n)) is None
        return n

    assert await load(1) == 1


async def test_methods_are_bound(manager: CacheManager) -> None:
    class Repo:
        def __init__(self) -> None:
            self.calls: list[int] = []

        @cached(ttl=60, manager=manager, key=lambda self, item_id: f"item:{item_id}")
        async def load(self, item_id: int) -> int:
            self.calls.append(item_id)
            return item_id

    repo = Repo()
    assert repo.load.cache_key(5) == "item:5"
    assert await repo.load(5) == 5
    assert await repo.load(5) == 5
    assert repo.calls == [5]
    assert await repo.load.invalidate(5) is True
    assert await repo.load(5) == 5
    assert repo.calls == [5, 5]
    assert isinstance(Repo.load, CachedFunction)
    assert Repo.load.cache_key(repo, 5) == "item:5"


async def test_a_method_without_a_key_names_the_problem(
    manager: CacheManager,
) -> None:
    class Repo:
        @cached(ttl=60, manager=manager)
        async def load(self, item_id: int) -> int:
            return item_id

    with pytest.raises(CacheXError, match="Pass key="):
        await Repo().load(1)


def test_the_wrapper_keeps_the_functions_identity(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager)
    async def load(n: int) -> int:
        """Load it."""
        return n

    assert load.__name__ == "load"
    assert load.__doc__ == "Load it."
    assert load.__wrapped__.__name__ == "load"
    assert isinstance(load, CachedFunction)


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        pytest.param({"ttl": 0}, ValueError, "ttl must be a positive", id="ttl-0"),
        pytest.param({"ttl": 1.5}, TypeError, "got float", id="ttl-float"),
        pytest.param(
            {"ttl": timedelta(seconds=1.5)},
            ValueError,
            "whole number of seconds",
            id="ttl-fraction",
        ),
        pytest.param({"lock": 1}, TypeError, "lock must be a bool", id="lock"),
        pytest.param({"key": 3}, CacheXError, "key must be a str", id="key"),
    ],
)
def test_bad_arguments_are_rejected_when_applied(
    kwargs: dict[str, Any], error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        cached(**kwargs)


class _Point(BaseModel):
    x: int
    y: int


async def test_a_model_result_comes_back_as_json_unless_a_type_is_given(
    manager: CacheManager,
) -> None:
    """Stored as pydantic writes it; `type_` turns it back, on a miss and a hit (#249)."""
    calls = 0

    @cached(ttl=60, manager=manager, type_=None)
    async def plain() -> _Point:
        return _Point(x=1, y=2)

    @cached(ttl=60, manager=manager, type_=_Point)
    async def typed() -> _Point:
        nonlocal calls
        calls += 1
        return _Point(x=1, y=2)

    assert await plain() == {"x": 1, "y": 2}  # type: ignore[comparison-overlap]
    assert await typed() == _Point(x=1, y=2)
    assert await typed() == _Point(x=1, y=2)
    assert calls == 1


# --- The return annotation is the type (#533) ---


async def test_a_model_annotation_gives_the_model_on_a_miss_and_on_a_hit(
    manager: CacheManager,
) -> None:
    calls = 0

    @cached(ttl=60, manager=manager)
    async def load() -> _Point:
        nonlocal calls
        calls += 1
        return _Point(x=1, y=2)

    assert await load() == _Point(x=1, y=2)
    assert await load() == _Point(x=1, y=2)
    assert calls == 1


async def test_a_sync_function_and_a_tuple_annotation(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager)
    def pair() -> tuple[int, int]:
        return (1, 2)

    assert await pair() == (1, 2)
    assert await pair() == (1, 2)


async def test_type_none_keeps_the_json_form(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager, type_=None)
    async def load() -> _Point:
        return _Point(x=1, y=2)

    assert await load() == {"x": 1, "y": 2}  # type: ignore[comparison-overlap]


async def test_an_explicit_type_wins_over_the_annotation(
    manager: CacheManager,
) -> None:
    @cached(ttl=60, manager=manager, type_=tuple[int, int])
    async def pair() -> list[int]:
        return [1, 2]

    assert await pair() == (1, 2)  # type: ignore[comparison-overlap]


async def test_a_cached_value_from_before_the_annotation_is_recomputed(
    manager: CacheManager,
) -> None:
    """A deploy that adds or changes the annotation needs no cache flush."""

    @cached(ttl=60, manager=manager, key="point")
    async def load() -> _Point:
        return _Point(x=1, y=2)

    await manager.set("point", {"x": "not a number"})

    assert await load() == _Point(x=1, y=2)


@pytest.mark.parametrize(
    "annotation",
    [
        Any,
        None,
        str,
        int,
        float,
        bool,
        object,
        list,
        dict,
        list[int],
        dict[str, Any],
        dict[str, list[str | None]],
        str | None,
        Optional[int],  # noqa: UP045 - the typing.Union spelling
        typing.List,  # noqa: UP006 - bare, without arguments
        typing.Dict,  # noqa: UP006
        typing.Dict[str, int],  # noqa: UP006
    ],
)
def test_annotations_the_json_form_already_has_are_not_validated(
    annotation: Any,
) -> None:
    def load() -> Any:
        return None

    load.__annotations__["return"] = annotation

    assert cached(ttl=60)(load)._type is None


@pytest.mark.parametrize(
    "annotation",
    [
        tuple[int, int],
        list[_Point],
        dict[str, _Point],
        dict[int, str],
        _Point | None,
        datetime,
        set[int],
    ],
)
def test_other_annotations_become_the_type(annotation: Any) -> None:
    def load() -> Any:
        return None

    load.__annotations__["return"] = annotation

    assert cached(ttl=60)(load)._type == annotation


def test_a_function_without_a_return_annotation_is_not_validated() -> None:
    @cached(ttl=60)
    async def load():
        return 1

    assert load._type is None


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from __future__ import annotations\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from decimal import Decimal\n"
            "def load() -> Decimal: ...\n",
            id="type-checking-import",
        ),
        pytest.param(
            "def load() -> NeverDefined: ...\n",
            id="lazy-annotation",
            marks=pytest.mark.skipif(
                sys.version_info < (3, 14),
                reason="annotations are evaluated when the function is defined",
            ),
        ),
    ],
)
def test_an_annotation_that_cannot_be_resolved_warns_and_keeps_the_json_form(
    source: str,
) -> None:
    """Otherwise this function would differ from its neighbours without a sign."""
    namespace: dict[str, Any] = {"__name__": "some_app"}
    exec(source, namespace)  # noqa: S102 - the test's own source

    with pytest.warns(UserWarning, match="cached return annotation") as record:
        decorated = cached(ttl=60)(namespace["load"])

    assert "cannot be resolved" in str(record[0].message)
    assert record[0].filename == __file__
    assert decorated._type is None
    # Either choice is made without a warning.
    assert cached(ttl=60, type_=None)(namespace["load"])._type is None
    assert cached(ttl=60, type_=int)(namespace["load"])._type is int


def test_an_unresolvable_parameter_alone_is_not_about_the_return_type() -> None:
    def load(point):
        return None

    load.__annotations__["point"] = "_DefinedLater"

    assert cached(ttl=60, key="k")(load)._type is None


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from __future__ import annotations\n"
            "from typing import TYPE_CHECKING, Any\n"
            "if TYPE_CHECKING:\n"
            "    from decimal import Decimal\n"
            "def load(amount: Decimal) -> dict[str, Any]: ...\n",
            id="unresolvable-parameter",
        ),
        pytest.param(
            "from typing import Any\n"
            "type Json = dict[str, Any] | list[Json] | None\n"
            "def load() -> Json: ...\n",
            id="type-alias",
            marks=pytest.mark.skipif(
                sys.version_info < (3, 12), reason="the type statement is 3.12+"
            ),
        ),
    ],
)
def test_real_modules_with_json_return_annotations_stay_quiet(source: str) -> None:
    """Only the return annotation counts, and an alias is read through."""
    namespace: dict[str, Any] = {"__name__": "some_app"}
    exec(source, namespace)  # noqa: S102 - the test's own source

    # No warning (filterwarnings = error would raise), and nothing to validate.
    assert cached(ttl=60, key="k")(namespace["load"])._type is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="the type statement is 3.12+")
def test_an_alias_of_a_model_is_validated_into() -> None:
    namespace: dict[str, Any] = {"__name__": "some_app", "Point": _Point}
    exec("type Points = list[Point]\ndef load() -> Points: ...\n", namespace)  # noqa: S102

    assert cached(ttl=60)(namespace["load"])._type is namespace["Points"]


class _NotForPydantic:
    """A plain class: pydantic cannot build a validator for it."""


@pytest.mark.parametrize(
    "annotation",
    [typing.Iterator[int], _NotForPydantic, Response, typing.NoReturn],
    ids=["iterator", "plain-class", "response", "noreturn"],
)
async def test_an_annotation_pydantic_cannot_validate_warns_and_keeps_the_json_form(
    manager: CacheManager, annotation: Any
) -> None:
    """Validating into it would raise on every call, hit or miss (#533)."""

    async def load() -> Any:
        return [1, 2]

    load.__annotations__["return"] = annotation

    with pytest.warns(UserWarning, match="cached return annotation") as record:
        decorated = cached(ttl=60, manager=manager)(load)

    assert "pydantic cannot validate" in str(record[0].message)
    assert record[0].filename == __file__
    assert decorated._type is None
    assert await decorated() == [1, 2]  # the miss
    assert await decorated() == [1, 2]  # the hit
    assert cached(ttl=60, type_=None)(load)._type is None  # no warning


def test_annotated_metadata_is_kept_for_a_type_that_is_validated() -> None:
    annotation = typing.Annotated[_Point, Field(title="a point")]

    def load() -> Any:
        return None

    load.__annotations__["return"] = annotation

    assert cached(ttl=60)(load)._type == annotation


def test_an_annotated_json_type_is_not_validated() -> None:
    """As 0.5.6 announced: only the type decides, not its metadata."""

    def load() -> typing.Annotated[int, Field(gt=0)]:
        return -5

    assert cached(ttl=60)(load)._type is None


async def test_a_key_callable_sees_the_defaults(manager: CacheManager) -> None:
    """Given only the passed arguments, a lambda's own default named the key (#577)."""
    calls: list[str] = []

    @cached(
        ttl=60,
        manager=manager,
        key=lambda user_id, locale="fr": f"u:{user_id}:{locale}",
    )
    async def load(user_id: int, locale: str = "en") -> str:
        calls.append(locale)
        return locale

    assert load.cache_key(1) == "u:1:en"
    assert await load(1) == "en"
    assert await load(1, "en") == "en"
    assert calls == ["en"]


async def test_a_key_callable_that_takes_fewer_arguments_still_works(
    manager: CacheManager,
) -> None:
    @cached(
        ttl=60,
        manager=manager,
        key=lambda user_id: f"u:{user_id}",
        tags=lambda user_id: [f"user:{user_id}"],
    )
    async def load(user_id: int, locale: str = "en") -> str:
        return locale

    assert load.cache_key(1) == "u:1"
    assert await load(1) == "en"


async def test_a_tags_callable_sees_the_defaults(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager, tags=lambda user_id, scope="x": [scope])
    async def load(user_id: int, scope: str = "users") -> int:
        return user_id

    await load(1)
    await manager.invalidate_tags("users")
    assert not await manager.has(load.cache_key(1))


async def test_a_template_naming_a_missing_argument_raises_cachexerror(
    manager: CacheManager,
) -> None:
    @cached(ttl=60, manager=manager, key="user:{uid}")
    async def load(user_id: int) -> int:
        return user_id

    with pytest.raises(CacheXError, match="key template"):
        load.cache_key(1)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_a_generator_function_is_refused(kind: str, manager: CacheManager) -> None:
    def numbers() -> typing.Iterator[int]:
        yield 1

    async def anumbers() -> AsyncIterator[int]:
        yield 1

    with pytest.raises(TypeError, match="generator"):
        cached(ttl=60, manager=manager)(numbers if kind == "sync" else anumbers)


async def test_arguments_pydantic_can_encode_get_a_typed_key(
    manager: CacheManager,
) -> None:
    """datetime, Decimal, UUID, bytes and sets raised before; now they are keyed (#577)."""

    @cached(ttl=60, manager=manager)
    async def load(value: object) -> int:
        return 1

    moment = datetime(2024, 1, 1, tzinfo=UTC)
    keys = {
        load.cache_key(value)
        for value in (
            moment,
            moment.isoformat(),
            Decimal("1.5"),
            "1.5",
            UUID(int=1),
            b"ab",
            "ab",
            {"b", "a"},
            frozenset({"a", "b"}),
            ["a", "b"],
        )
    }
    assert len(keys) == 10
    assert load.cache_key({"b", "a"}) == load.cache_key({"a", "b"})
    # JSON's own types keep the keys they had.
    assert load.cache_key("ab") == f"{load.__module__}.{load.__qualname__}:" + _digest(
        value="ab"
    )
    assert await load(moment) == 1


async def test_an_iterator_argument_is_refused_and_not_used_up(
    manager: CacheManager,
) -> None:
    """Hashing it read it all, and the function then ran on nothing (#577)."""

    @cached(ttl=60, manager=manager)
    async def total(values: object) -> int:
        return 0

    items = iter([1, 2])
    with pytest.raises(CacheXError, match="iterator"):
        await total(items)
    assert list(items) == [1, 2]


def test_nested_set_keys_are_the_same_in_every_process() -> None:
    script = (
        "from fastapi_cachex import cached\n"
        "@cached(ttl=60)\n"
        "async def f(v: object) -> int: return 1\n"
        "print(f.cache_key([frozenset({'a', 'b', frozenset({'c', 'd', 'e'})})]))\n"
    )
    keys = {
        subprocess.run(  # noqa: S603 - our own script, not input
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2", "3")
    }
    assert len(keys) == 1


async def test_any_bytes_argument_is_keyed(manager: CacheManager) -> None:
    @cached(ttl=60, manager=manager)
    async def load(value: bytes) -> int:
        return 1

    assert load.cache_key(b"\xff") != load.cache_key(b"\xfe")


@pytest.mark.skipif(sys.version_info < (3, 14), reason="lazy annotations")
async def test_a_key_callable_with_an_unresolvable_annotation_works(
    manager: CacheManager,
) -> None:
    """A name imported under TYPE_CHECKING raised NameError on 3.14."""
    namespace: dict[str, Any] = {}
    exec("def key(user_id: Missing) -> str:\n    return f'u:{user_id}'", namespace)  # noqa: S102

    @cached(ttl=60, manager=manager, key=namespace["key"])
    async def load(user_id: int) -> int:
        return user_id

    assert load.cache_key(1) == "u:1"


def test_a_wrapper_collecting_a_generator_is_accepted(manager: CacheManager) -> None:
    """Only the function itself is checked: this one returns a list."""

    def listify(
        func: typing.Callable[[], typing.Iterator[int]],
    ) -> typing.Callable[[], list[int]]:
        @functools.wraps(func)
        def wrapper() -> list[int]:
            return list(func())

        return wrapper

    @listify
    def numbers() -> typing.Iterator[int]:
        yield 1

    cached(ttl=60, manager=manager, type_=list[int])(numbers)


class _Rich(BaseModel):
    """A model whose fields can hold no secret, with awkward encodings."""

    data: bytes = b"\x00hi"
    ratio: float = float("inf")
    span: timedelta = timedelta(seconds=90)
    when: datetime = datetime(2024, 1, 1, tzinfo=UTC)
    amount: Decimal = Decimal("1.10")
    ident: UUID = UUID(int=1)
    tags: set[int] = Field(default_factory=lambda: {3, 1})
    rows: list[_Count] = Field(default_factory=lambda: [_Count(count=1)])
    aliased_: int = Field(1, alias="aliased")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def doubled(self) -> int:
        return 2


def test_a_plain_model_skips_the_search_and_keeps_its_key() -> None:
    """#668: its type rules out a secret, and its JSON form is unchanged."""
    from pydantic_core import to_jsonable_python

    from fastapi_cachex.cached import _model_json
    from fastapi_cachex.cached import _plain_model

    model = _Rich()

    assert _plain_model(_Rich)
    assert _model_json(model) == to_jsonable_python(model, bytes_mode="base64")


class _Row(BaseModel):
    id: int
    name: str


class _Validated(BaseModel):
    count: int

    @field_validator("count")
    @classmethod
    def _swap(cls, count: int) -> object:
        return SecretStr(str(count))


class _Computed(BaseModel):
    tenant: str = "a"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def token(self) -> SecretStr:
        return SecretStr(self.tenant)


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Wrapped(BaseModel):
    token: str

    @field_serializer("token")
    def _wrap(self, token: str) -> SecretStr:
        return SecretStr(token)


def _assigned() -> _Row:
    row = _Row(id=1, name="a")
    row.name = SecretStr("tenant-a")  # type: ignore[assignment]
    return row


@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize(
    "argument",
    [
        lambda: _Row.model_construct(id=1, name=SecretStr("tenant-a")),  # type: ignore[arg-type]
        _assigned,
        lambda: _Validated(count=1),
        _Computed,
        lambda: _Open(token=SecretStr("tenant-a")),
        lambda: _Wrapped(token="tenant-a"),
        lambda: [_Row(id=1, name="a"), _Row.model_construct(id=2, name=SecretStr("b"))],  # type: ignore[arg-type]
    ],
    ids=[
        "construct",
        "assignment",
        "validator",
        "computed",
        "extra",
        "serializer",
        "in-a-list",
    ],
)
async def test_a_secret_where_the_type_rules_none_out_is_still_refused(
    manager: CacheManager, argument: Callable[[], object]
) -> None:
    """A value that does not fit its field's type falls back to the search."""

    @cached(ttl=60, manager=manager)
    async def load(item: object) -> int:
        return 1

    with pytest.raises(CacheXError, match="pydantic secret"):
        load.cache_key(argument())


class _Login(BaseModel):
    user: str
    password: SecretStr = Field(exclude=True)


async def test_a_secret_in_an_excluded_field_is_left_out_as_before(
    manager: CacheManager,
) -> None:
    """Not in the key, as in 0.7.2: calls that differ only in it share it."""
    from fastapi_cachex.cached import _plain_model

    assert _plain_model(_Login)  # the field is not written out

    @cached(ttl=60, manager=manager)
    async def load(login: _Login) -> str:
        return login.user

    assert load.cache_key(_Login(user="a", password=SecretStr("1"))) == load.cache_key(
        _Login(user="a", password=SecretStr("2"))
    )
