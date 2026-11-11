"""``@cached`` caches a plain function's result through ``CacheManager`` (#248)."""

import asyncio
import hashlib
import json
import sys
import typing
from collections.abc import AsyncIterator
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Optional

import pytest
import pytest_asyncio
from pydantic import BaseModel
from pydantic import Field
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
