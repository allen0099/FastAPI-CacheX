"""Tests for the CacheLock distributed lock helper."""

import asyncio
import time

import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.exceptions import LockTimeoutError
from fastapi_cachex.lock import CacheLock
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry


@pytest.mark.asyncio
async def test_lock_basic_context_manager() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    async with CacheLock("test_job", ttl=30) as lock:
        assert lock.name == "test_job"
        assert lock.key == "lock:test_job"
        assert await lock.locked() is True
        assert await backend.get("lock:test_job") is not None

    assert await backend.get("lock:test_job") is None


@pytest.mark.asyncio
async def test_lock_acquire_non_blocking() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock1 = CacheLock("job", ttl=30)
    lock2 = CacheLock("job", ttl=30)

    assert await lock1.acquire(blocking=False) is True
    assert await lock2.acquire(blocking=False) is False
    assert await lock1.locked() is True

    assert await lock1.release() is True
    assert await lock2.release() is False
    assert await lock1.locked() is False


@pytest.mark.asyncio
async def test_lock_acquire_blocking_indefinite_retries_until_available() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock1 = CacheLock("job", ttl=30)
    lock2 = CacheLock("job", ttl=30, poll_interval=0.01)

    assert await lock1.acquire(blocking=False) is True

    async def release_later() -> None:
        await asyncio.sleep(0.03)
        await lock1.release()

    task = asyncio.create_task(release_later())
    assert await lock2.acquire(blocking=True, poll_interval=0.01) is True
    await task
    assert await lock2.release() is True


@pytest.mark.asyncio
async def test_lock_acquire_blocking_timeout_returns_false() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock1 = CacheLock("job", ttl=30)
    lock2 = CacheLock("job", ttl=30, timeout=0.05, poll_interval=0.01)

    assert await lock1.acquire(blocking=False) is True
    assert await lock2.acquire(blocking=True) is False
    assert await lock1.release() is True


@pytest.mark.asyncio
async def test_lock_context_manager_timeout_raises_lock_timeout_error() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock1 = CacheLock("job", ttl=30)
    assert await lock1.acquire(blocking=False) is True

    with pytest.raises(LockTimeoutError, match="Failed to acquire lock 'job'"):
        async with CacheLock("job", ttl=30, timeout=0.05, poll_interval=0.01):
            pass

    assert await lock1.release() is True


@pytest.mark.asyncio
async def test_lock_extend() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock1 = CacheLock("job", ttl=30)
    lock2 = CacheLock("job", ttl=30)

    assert await lock1.acquire(blocking=False) is True
    assert await lock1.extend(60) is True
    assert await lock2.extend(60) is False

    assert await lock1.release() is True
    assert await lock1.extend(60) is False


@pytest.mark.asyncio
async def test_lock_locked_status() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock = CacheLock("job")
    assert await lock.locked() is False

    await lock.acquire(blocking=False)
    assert await lock.locked() is True

    await lock.release()
    assert await lock.locked() is False


@pytest.mark.asyncio
async def test_lock_custom_key_prefix() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock = CacheLock("custom", key_prefix="custom_lock:")
    assert lock.key == "custom_lock:custom"

    async with lock:
        assert await backend.get("custom_lock:custom") is not None

    assert await backend.get("custom_lock:custom") is None


@pytest.mark.asyncio
async def test_lock_custom_backend() -> None:
    default_backend = MemoryBackend()
    custom_backend = MemoryBackend()
    BackendProxy.set(default_backend)

    lock = CacheLock("job", backend=custom_backend)
    async with lock:
        assert await custom_backend.get("lock:job") is not None
        assert await default_backend.get("lock:job") is None

    assert await custom_backend.get("lock:job") is None


@pytest.mark.asyncio
async def test_lock_release_on_expired_lock_returns_false() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock = CacheLock("job", ttl=30)
    assert await lock.acquire(blocking=False) is True

    # Simulate TTL expiration
    backend.cache["lock:job"].expiry = time.time() - 1

    assert await lock.release() is False


@pytest.mark.asyncio
async def test_lock_token_uniqueness() -> None:
    lock1 = CacheLock("job")
    lock2 = CacheLock("job")

    assert lock1._token != lock2._token
    assert lock1._entry != lock2._entry


@pytest.mark.asyncio
async def test_lock_raises_backend_not_found_if_no_backend_set() -> None:
    BackendProxy.set(None)
    lock = CacheLock("job")

    with pytest.raises(BackendNotFoundError):
        await lock.acquire(blocking=False)


@pytest.mark.asyncio
async def test_lock_reentry_raises_runtime_error() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock = CacheLock("job", ttl=30)
    assert await lock.acquire(blocking=False) is True

    with pytest.raises(
        RuntimeError,
        match="This CacheLock instance is already held",
    ):
        await lock.acquire(blocking=False)

    assert await lock.release() is True


class YieldingMemoryBackend(MemoryBackend):
    async def set_if_absent(
        self, key: str, value: CacheEntry, ttl: int | None = None
    ) -> bool:
        await asyncio.sleep(0)
        return await super().set_if_absent(key, value, ttl=ttl)


@pytest.mark.asyncio
async def test_lock_shared_instance_raises_runtime_error() -> None:
    backend = YieldingMemoryBackend()
    BackendProxy.set(backend)

    lock = CacheLock("shared_job", ttl=30)

    async def task_worker() -> None:
        async with lock:
            await asyncio.sleep(0.05)

    results = await asyncio.gather(task_worker(), task_worker(), return_exceptions=True)
    assert any(isinstance(res, RuntimeError) for res in results)
    assert any(res is None for res in results)


@pytest.mark.asyncio
async def test_lock_acquire_cancelled_resets_is_held() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)

    lock1 = CacheLock("job", ttl=30)
    lock2 = CacheLock("job", ttl=30, poll_interval=0.01)

    assert await lock1.acquire(blocking=False) is True

    task = asyncio.create_task(lock2.acquire(blocking=True, poll_interval=0.01))
    await asyncio.sleep(0.02)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await lock1.release()

    assert await lock2.acquire(blocking=False) is True
    assert await lock2.release() is True

