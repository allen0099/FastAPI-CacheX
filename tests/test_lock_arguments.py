"""`CacheLock` checks its waiting arguments (#578)."""

import asyncio
import math
from fractions import Fraction

import pytest

from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.lock import CacheLock


@pytest.mark.parametrize("value", [0, -1, 0.0, math.nan, math.inf, 10**400])
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


async def test_a_nan_timeout_is_refused() -> None:
    """NaN waited forever: no elapsed time compares as reaching it."""
    with pytest.raises(ValueError, match="timeout"):
        CacheLock("job", timeout=math.nan, backend=MemoryBackend())
    with pytest.raises(ValueError, match="timeout"):
        await CacheLock("job", backend=MemoryBackend()).acquire(timeout=math.nan)


@pytest.mark.parametrize(("name", "value"), [("poll_interval", True), ("timeout", "1")])
def test_a_wait_argument_that_is_not_a_number_is_refused(
    name: str, value: object
) -> None:
    with pytest.raises(TypeError, match=name):
        CacheLock("job", backend=MemoryBackend(), **{name: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, -1, -0.5])
async def test_a_zero_or_negative_timeout_tries_once(value: float) -> None:
    """A deadline that has passed gives False, as before, not an error."""
    backend = MemoryBackend()
    async with CacheLock("job", backend=backend):
        assert not await asyncio.create_task(
            CacheLock("job", backend=backend, timeout=value).acquire()
        )
        assert not await asyncio.create_task(
            CacheLock("job", backend=backend).acquire(timeout=value)
        )


async def test_other_real_numbers_are_accepted() -> None:
    lock = CacheLock(
        "job",
        # Annotated float, but any real number worked before #578.
        timeout=Fraction(1, 2),  # type: ignore[arg-type]
        poll_interval=Fraction(1, 100),  # type: ignore[arg-type]
        backend=MemoryBackend(),
    )
    assert await lock.acquire()
    assert await lock.release()
