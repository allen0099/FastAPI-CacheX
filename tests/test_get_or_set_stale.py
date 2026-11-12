"""``get_or_set(stale_ttl=...)`` and ``@cached(stale_ttl=...)`` (#559).

Time is moved by patching ``fastapi_cachex._stored_response._now``; the memory
backend keeps real time, so a value stays in it while the patched clock runs
ahead.
"""

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from fastapi_cachex import CacheManager
from fastapi_cachex import _stored_response as stored_response
from fastapi_cachex import cached
from fastapi_cachex import set_enabled
from fastapi_cachex import set_event_handler
from fastapi_cachex._coalesce import _REFRESHING
from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.backends.codec import encode_entry
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import BackendUnavailableError
from fastapi_cachex.lock import CacheLock
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

TTL = 60
STALE_TTL = 30


class _Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(stored_response, "_now", clock)
    return clock


class _Factory:
    """Counts runs; ``fail`` makes it raise, ``gate`` makes it wait."""

    def __init__(self) -> None:
        self.runs = 0
        self.fail = False
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def __call__(self) -> str:
        self.runs += 1
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            msg = "upstream is down"
            raise RuntimeError(msg)
        return f"run {self.runs}"


@pytest.fixture
def factory() -> _Factory:
    return _Factory()


@pytest.fixture
def manager() -> CacheManager:
    return CacheManager(BackendProxy.get())


async def _get(manager: CacheManager, factory: _Factory, **kwargs: Any) -> Any:
    options = {"ttl": TTL, "stale_ttl": STALE_TTL, **kwargs}
    return await manager.get_or_set("k", factory, **options)


def _seconds_left() -> float:
    """How much longer the memory backend keeps the value."""
    expiry = BackendProxy.get().cache["cache:k"].expiry  # type: ignore[attr-defined]
    assert expiry is not None
    return float(expiry - time.time())


async def test_a_value_is_kept_for_the_ttl_plus_the_stale_window(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    assert await _get(manager, factory) == "run 1"

    assert TTL + STALE_TTL - 2 < _seconds_left() <= TTL + STALE_TTL
    clock.now += TTL - 1
    assert await _get(manager, factory) == "run 1"
    assert factory.runs == 1


async def test_past_the_ttl_the_first_caller_refreshes(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await _get(manager, factory)
    clock.now += TTL

    assert await _get(manager, factory, ttl=timedelta(seconds=TTL)) == "run 2"

    assert await _get(manager, factory) == "run 2"
    assert factory.runs == 2
    assert TTL + STALE_TTL - 2 < _seconds_left() <= TTL + STALE_TTL
    assert set() == _REFRESHING
    assert not await CacheLock("cache:k").locked()


@pytest.mark.parametrize("lock", [True, False])
async def test_callers_during_a_refresh_get_the_stale_value_at_once(
    manager: CacheManager, factory: _Factory, clock: _Clock, lock: bool
) -> None:
    await _get(manager, factory, lock=lock)
    clock.now += TTL + 1
    factory.gate = asyncio.Event()
    factory.started.clear()
    refresher = asyncio.ensure_future(_get(manager, factory, lock=lock))
    await asyncio.wait_for(factory.started.wait(), timeout=5)

    others = await asyncio.wait_for(
        asyncio.gather(*[_get(manager, factory, lock=lock) for _ in range(3)]),
        timeout=5,
    )

    assert others == ["run 1"] * 3
    factory.gate.set()
    assert await refresher == "run 2"
    assert factory.runs == 2


async def test_a_refresh_running_in_another_process_is_not_repeated(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await _get(manager, factory)
    clock.now += TTL + 1
    other = CacheLock("cache:k")
    assert await other.acquire(blocking=False)

    assert await _get(manager, factory) == "run 1"
    assert factory.runs == 1
    # Without the lock nothing says another process is at it.
    assert await _get(manager, factory, lock=False) == "run 2"

    assert await other.release()
    assert set() == _REFRESHING


async def test_a_failing_refresh_returns_the_stale_value(
    manager: CacheManager,
    factory: _Factory,
    clock: _Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _get(manager, factory)
    clock.now += TTL + 1
    factory.fail = True

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.manager"):
        assert await _get(manager, factory) == "run 1"

    assert "Refreshing a stale value failed (RuntimeError: upstream is down)" in (
        caplog.text
    )
    assert "Traceback" not in caplog.text
    assert set() == _REFRESHING
    assert not await CacheLock("cache:k").locked()
    # The next caller tries again, and a working factory replaces the value.
    factory.fail = False
    assert await _get(manager, factory) == "run 3"


async def test_a_failed_refresh_is_a_miss_and_a_stale_event(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await _get(manager, factory)
    clock.now += TTL + 1
    factory.fail = True
    events: list[str] = []
    set_event_handler(lambda event: events.append(event.kind))
    try:
        await _get(manager, factory)
        # Another process is at it: stale, and the factory does not run.
        other = CacheLock("cache:k")
        assert await other.acquire(blocking=False)
        await _get(manager, factory)
        await other.release()
    finally:
        set_event_handler(None)

    assert events == ["miss", "stale", "stale"]


class _NoWrites(MemoryBackend):
    async def set(self, *args: Any, **kwargs: Any) -> None:
        msg = "backend is down"
        raise BackendUnavailableError(msg)


async def test_only_a_failure_of_the_factory_itself_is_answered_stale(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await _get(manager, factory, type_=str)
    clock.now += TTL + 1

    async def wrong_type() -> int:
        return 1

    # As on a miss: the value was computed, and does not fit.
    with pytest.raises(ValidationError):
        await manager.get_or_set("k", wrong_type, TTL, stale_ttl=STALE_TTL, type_=str)
    assert set() == _REFRESHING
    assert not await CacheLock("cache:k").locked()

    # A backend that cannot store is reported and raised, as on a miss.
    down = _NoWrites()
    down.cache = BackendProxy.get().cache  # type: ignore[attr-defined]
    await manager.set("k", "old", ttl=TTL)
    entry = await BackendProxy.get().get("cache:k")
    assert entry is not None
    entry.stale_at = clock.now - 1
    await BackendProxy.get().set("cache:k", entry, ttl=TTL)
    events: list[str] = []
    set_event_handler(lambda event: events.append(event.kind))
    try:
        with pytest.raises(BackendUnavailableError):
            await _get(CacheManager(down, lock=False), factory)
    finally:
        set_event_handler(None)
    assert events == ["miss", "error"]


async def test_a_refresh_that_asks_for_its_own_key_does_not_wait_for_itself(
    manager: CacheManager, clock: _Clock
) -> None:
    async def inner() -> str:
        return "inner"

    async def outer() -> str:
        return await asyncio.wait_for(manager.get_or_set("k", inner, TTL), timeout=2)

    await manager.set("k", "old", ttl=TTL)
    entry = await BackendProxy.get().get("cache:k")
    assert entry is not None
    entry.stale_at = clock.now - 1
    await BackendProxy.get().set("cache:k", entry, ttl=TTL)

    assert await manager.get_or_set("k", outer, TTL, stale_ttl=STALE_TTL) == "inner"


async def test_a_cancelled_refresh_is_cancelled_and_leaves_nothing_held(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await _get(manager, factory)
    clock.now += TTL + 1
    factory.gate = asyncio.Event()
    factory.started.clear()
    refresher = asyncio.ensure_future(_get(manager, factory))
    await asyncio.wait_for(factory.started.wait(), timeout=5)

    refresher.cancel()

    with pytest.raises(asyncio.CancelledError):
        await refresher
    assert set() == _REFRESHING
    assert not await CacheLock("cache:k").locked()


async def test_tags_and_stale_ttl_together_store_the_value_itself(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    assert await _get(manager, factory, tags=["t"]) == "run 1"

    entry = await BackendProxy.get().get("cache:k")
    assert entry is not None
    assert (entry.content, entry.stale_at) == (b'"run 1"', clock.now + TTL)
    assert [name for name, _ in entry.tags] == ["t"]
    clock.now += TTL + 1
    assert await _get(manager, factory, tags=["t"]) == "run 2"


async def test_past_the_stale_window_a_failure_is_raised(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    factory.fail = True
    with pytest.raises(RuntimeError, match="upstream is down"):
        await _get(manager, factory)

    factory.fail = False
    await _get(manager, factory)
    await BackendProxy.get().delete("cache:k")  # what the backend does by then
    factory.fail = True
    with pytest.raises(RuntimeError, match="upstream is down"):
        await _get(manager, factory)


async def test_events_tell_a_stale_value_from_a_hit(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await _get(manager, factory)
    clock.now += TTL + 1
    factory.gate = asyncio.Event()
    factory.started.clear()
    events: list[str] = []
    set_event_handler(lambda event: events.append(event.kind))
    try:
        refresher = asyncio.ensure_future(_get(manager, factory))
        await asyncio.wait_for(factory.started.wait(), timeout=5)
        await _get(manager, factory)
        factory.gate.set()
        await refresher
        await _get(manager, factory)
    finally:
        set_event_handler(None)

    assert events == ["miss", "stale", "store", "hit"]


async def test_an_invalidated_tag_is_a_miss_not_a_stale_value(
    manager: CacheManager, factory: _Factory
) -> None:
    await _get(manager, factory, tags=["t"])
    factory.fail = True
    await manager.invalidate_tags("t")

    with pytest.raises(RuntimeError):
        await _get(manager, factory, tags=["t"])


async def test_every_other_read_takes_a_stale_value_as_a_miss(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await _get(manager, factory)
    clock.now += TTL - 1
    assert await manager.get("k") == "run 1"
    assert await manager.has("k")
    assert await manager.get_many(["k"]) == {"k": "run 1"}
    assert not await manager.add("k", "theirs", ttl=TTL)

    clock.now += 1

    assert await manager.get("k") is None
    assert not await manager.has("k")
    assert await manager.get_many(["k"]) == {}
    # Without stale_ttl the call knows nothing of stale values: it computes.
    assert await manager.get_or_set("k", factory, ttl=TTL) == "run 2"
    assert factory.runs == 2


async def test_add_replaces_a_stale_value(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await _get(manager, factory)
    clock.now += TTL

    assert await manager.add("k", "theirs", ttl=TTL)
    assert await manager.get("k") == "theirs"


async def test_a_value_written_without_stale_ttl_never_turns_stale(
    manager: CacheManager, factory: _Factory, clock: _Clock
) -> None:
    await manager.set("k", "set", ttl=TTL)
    clock.now += TTL + 1  # the memory backend still has it, on its own clock

    assert await _get(manager, factory) == "set"
    assert factory.runs == 0


@pytest.mark.parametrize("stale_at", [None, 1794000000.5])
def test_the_codec_keeps_stale_at(stale_at: float | None) -> None:
    entry = CacheEntry(fingerprint="f", content=b"1", stale_at=stale_at)

    encoded = encode_entry(entry)

    assert decode_entry(encoded) == entry
    # An entry without it is stored as before.
    assert (b"stale_at" in encoded) is (stale_at is not None)


async def test_stale_ttl_needs_a_ttl(factory: _Factory) -> None:
    manager = CacheManager(BackendProxy.get())
    with pytest.raises(ValueError, match="stale_ttl needs a ttl"):
        await manager.get_or_set("k", factory, stale_ttl=30)
    with pytest.raises(TypeError):
        await manager.get_or_set("k", factory, ttl=60, stale_ttl=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"greater than|positive"):
        await manager.get_or_set("k", factory, ttl=60, stale_ttl=0)

    with_default = CacheManager(BackendProxy.get(), default_ttl=TTL)
    assert await with_default.get_or_set("k", factory, stale_ttl=30) == "run 1"
    assert TTL + STALE_TTL - 2 < _seconds_left() <= TTL + STALE_TTL


async def test_switched_off_nothing_is_read_or_kept(
    manager: CacheManager, factory: _Factory
) -> None:
    set_enabled(False)

    assert [await _get(manager, factory) for _ in range(2)] == ["run 1", "run 2"]
    assert await BackendProxy.get().get_all_keys() == []


async def test_cached_passes_stale_ttl_on(clock: _Clock) -> None:
    runs = 0

    @cached(ttl=TTL, stale_ttl=STALE_TTL, key="k")
    async def load() -> int:
        nonlocal runs
        runs += 1
        if runs == 2:
            msg = "upstream is down"
            raise RuntimeError(msg)
        return runs

    assert await load() == 1
    clock.now += TTL + 1
    assert await load() == 1  # the function raised; the stale value answers
    assert await load() == 3
    with pytest.raises(TypeError):
        cached(ttl=TTL, stale_ttl=1.5)  # type: ignore[arg-type]
