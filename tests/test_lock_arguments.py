"""`CacheLock` checks its waiting arguments (#578)."""

import asyncio
import math

import pytest

from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.lock import CacheLock


@pytest.mark.parametrize("value", [0, -1, 0.0, math.nan, math.inf])
async def test_a_poll_interval_that_would_spin_or_hang_is_refused(value: float) -> None:
    """Zero retried in a tight loop: about 49,000 claims in half a second."""
    with pytest.raises(ValueError, match="poll_interval"):
        CacheLock("job", poll_interval=value, backend=MemoryBackend())
    lock = CacheLock("job", backend=MemoryBackend())
    with pytest.raises(ValueError, match="poll_interval"):
        await lock.acquire(poll_interval=value)
    # A refused override leaves the instance usable.
    assert await lock.acquire()
    assert await lock.release()


@pytest.mark.parametrize("value", [-1, -0.5, math.nan])
async def test_a_negative_timeout_is_refused(value: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        CacheLock("job", timeout=value, backend=MemoryBackend())
    with pytest.raises(ValueError, match="timeout"):
        await CacheLock("job", backend=MemoryBackend()).acquire(timeout=value)


@pytest.mark.parametrize(("name", "value"), [("poll_interval", True), ("timeout", "1")])
def test_a_wait_argument_that_is_not_a_number_is_refused(
    name: str, value: object
) -> None:
    with pytest.raises(TypeError, match=name):
        CacheLock("job", backend=MemoryBackend(), **{name: value})  # type: ignore[arg-type]


async def test_a_zero_timeout_tries_once() -> None:
    backend = MemoryBackend()
    async with CacheLock("job", backend=backend):
        assert not await asyncio.create_task(
            CacheLock("job", backend=backend, timeout=0).acquire()
        )
