"""``CacheLock(auto_extend=True)`` and the ``lost`` flag (#250).

The renewal runs every third of the ``ttl``, so these tests use ``ttl=1``
(the smallest) and wait in fractions of a second.
"""

import asyncio
import logging
import time
from datetime import timedelta

import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.lock import CacheLock
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import log_ref

_SECRET_NAME = "user:alice@example.com"


def _lost(lock: CacheLock) -> bool:
    """Read through a call: mypy would keep the value narrowed across awaits."""
    return lock.lost


class CountingBackend(MemoryBackend):
    """Counts renewals, and can be told to fail them."""

    def __init__(self) -> None:
        super().__init__()
        self.renewals = 0
        self.fail = False

    async def expire_if_equals(
        self, key: str, expected: CacheEntry, ttl: int | timedelta
    ) -> bool:
        self.renewals += 1
        if self.fail:
            msg = "backend unreachable"
            raise ConnectionError(msg)
        return await super().expire_if_equals(key, expected, ttl)


async def test_the_lock_outlives_its_ttl_while_it_is_held() -> None:
    backend = CountingBackend()
    other = CacheLock("job", ttl=1, backend=backend, blocking=False)

    async with CacheLock("job", ttl=1, backend=backend, auto_extend=True) as lock:
        await asyncio.sleep(1.5)
        # Past the ttl, and still exclusive.
        assert await other.acquire() is False
        assert not _lost(lock)

    assert backend.renewals >= 3
    assert not _lost(lock)
    assert await other.acquire() is True


async def test_without_auto_extend_nothing_renews_the_lock() -> None:
    backend = CountingBackend()

    async with CacheLock("job", ttl=1, backend=backend):
        await asyncio.sleep(0.5)

    assert backend.renewals == 0


async def test_release_stops_the_renewal() -> None:
    backend = CountingBackend()
    lock = CacheLock("job", ttl=1, backend=backend, auto_extend=True)
    await lock.acquire()
    await lock.release()

    await asyncio.sleep(0.5)

    assert backend.renewals == 0
    assert await backend.get(lock.key) is None


async def test_the_renewal_uses_the_ttl_the_lock_was_acquired_with() -> None:
    backend = CountingBackend()
    lock = CacheLock("job", ttl=60, backend=backend, auto_extend=True)
    await lock.acquire(ttl=1)
    try:
        await asyncio.sleep(0.5)  # ttl / 3 of the override, not of 60
        assert backend.renewals >= 1
        expiry = backend.cache[lock.key].expiry
        assert expiry is not None
        assert expiry - time.time() <= 1
    finally:
        await lock.release()


async def test_a_lock_taken_by_someone_else_is_reported_lost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = CountingBackend()
    lock = CacheLock(_SECRET_NAME, ttl=1, backend=backend, auto_extend=True)
    await lock.acquire()
    # The lock expires behind the holder's back and another caller takes it.
    await backend.delete(lock.key)
    thief = CacheLock(_SECRET_NAME, ttl=30, backend=backend, blocking=False)
    assert await thief.acquire() is True

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.lock"):
        await asyncio.sleep(0.5)

    assert _lost(lock)
    assert log_ref(lock.key) in caplog.text
    assert _SECRET_NAME not in caplog.text
    # The renewal has stopped, and releasing leaves the other holder's lock.
    renewals = backend.renewals
    await asyncio.sleep(0.5)
    assert backend.renewals == renewals
    assert await lock.release() is False
    assert await thief.locked() is True


async def test_a_backend_that_stays_down_for_a_ttl_means_the_lock_is_lost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = CountingBackend()
    lock = CacheLock("job", ttl=1, backend=backend, auto_extend=True)
    await lock.acquire()
    backend.fail = True

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.lock"):
        await asyncio.sleep(0.5)
        # One failure is retried: the lock has not expired yet.
        assert not _lost(lock)
        await asyncio.sleep(1.0)

    assert _lost(lock)
    assert "could not renew" in caplog.text
    backend.fail = False
    await lock.release()


async def test_a_failed_renewal_is_retried() -> None:
    backend = CountingBackend()
    lock = CacheLock("job", ttl=2, backend=backend, auto_extend=True)
    await lock.acquire()
    backend.fail = True
    await asyncio.sleep(0.9)  # the renewal at 0.67 s fails
    backend.fail = False
    await asyncio.sleep(0.9)  # the one at 1.33 s succeeds, before the ttl

    assert not _lost(lock)
    assert await lock.release() is True


class HangingBackend(CountingBackend):
    """A renewal that never returns: a blackholed connection without a timeout."""

    async def expire_if_equals(
        self, key: str, expected: CacheEntry, ttl: int | timedelta
    ) -> bool:
        await asyncio.sleep(3600)
        return True


async def test_a_renewal_that_hangs_past_the_ttl_means_the_lock_is_lost() -> None:
    backend = HangingBackend()
    lock = CacheLock("job", ttl=1, backend=backend, auto_extend=True)
    await lock.acquire()

    await asyncio.sleep(1.3)

    assert _lost(lock)
    await lock.release()


async def test_a_slow_backend_does_not_stretch_the_gap_between_renewals() -> None:
    """Each renewal is due a third of the ttl after the last was sent."""

    class SlowBackend(CountingBackend):
        async def expire_if_equals(
            self, key: str, expected: CacheEntry, ttl: int | timedelta
        ) -> bool:
            await asyncio.sleep(0.25)
            return await super().expire_if_equals(key, expected, ttl)

    backend = SlowBackend()
    lock = CacheLock("job", ttl=1, backend=backend, auto_extend=True)
    await lock.acquire()

    await asyncio.sleep(2.5)

    # Due at 0.33, 0.67, ... 2.33 s: seven. Sleeping a full interval after
    # each 0.25 s reply instead would have sent four.
    assert backend.renewals >= 6
    assert not _lost(lock)
    assert await lock.release() is True


async def test_a_non_blocking_acquire_starts_the_renewal_too() -> None:
    backend = CountingBackend()
    lock = CacheLock("job", ttl=1, backend=backend, blocking=False, auto_extend=True)
    assert await lock.acquire() is True

    await asyncio.sleep(0.5)

    assert backend.renewals >= 1
    await lock.release()


async def test_the_ttl_without_a_renewal_counts_from_the_last_success() -> None:
    """An outage late in a long hold is still given a whole ttl."""
    backend = CountingBackend()
    lock = CacheLock("job", ttl=1, backend=backend, auto_extend=True)
    await lock.acquire()
    await asyncio.sleep(1.1)  # held for longer than the ttl, renewed thrice
    backend.fail = True
    await asyncio.sleep(0.4)  # one failed renewal

    assert not _lost(lock)
    backend.fail = False
    assert await lock.release() is True


async def test_lost_is_set_by_a_manual_extend_or_release_and_reset_by_acquire() -> None:
    backend = MemoryBackend()
    lock = CacheLock("job", ttl=30, backend=backend, blocking=False)
    assert not _lost(lock)

    await lock.acquire()
    await backend.delete(lock.key)
    assert await lock.extend() is False
    assert _lost(lock)

    await lock.release()
    await lock.acquire()
    assert not _lost(lock)
    await backend.delete(lock.key)
    assert await lock.release() is False
    assert _lost(lock)


async def test_releasing_twice_or_without_acquiring_is_not_a_lost_lock() -> None:
    backend = MemoryBackend()
    lock = CacheLock("job", ttl=30, backend=backend)

    assert await lock.release() is False
    assert await lock.extend() is False
    assert not _lost(lock)

    await lock.acquire()
    assert await lock.release() is True
    assert await lock.release() is False
    assert not _lost(lock)


async def test_release_cancels_the_renewal_and_forgets_it() -> None:
    backend = CountingBackend()
    lock = CacheLock("job", ttl=1, backend=backend, auto_extend=True)
    await lock.acquire()
    renewal = lock._renewal
    assert renewal is not None

    await lock.release()

    assert renewal.cancelled()
    assert lock._renewal is None
