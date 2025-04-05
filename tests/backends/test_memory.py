import asyncio

import pytest
import pytest_asyncio

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.types import ETagContent


@pytest_asyncio.fixture
def memory_backend():
    return MemoryBackend()


@pytest.mark.asyncio
async def test_memory_backend_set_get(memory_backend: MemoryBackend):
    key = "test_key"
    value = ETagContent(
        etag="test_etag",
        content={
            "response": b"test_value",
            "media_type": "application/json",
        },
    )
    ttl = 60

    await memory_backend.set(key, value, ttl)
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value == value


@pytest.mark.asyncio
async def test_memory_backend_get_nonexistent_key(memory_backend: MemoryBackend):
    key = "nonexistent_key"
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value is None


@pytest.mark.asyncio
async def test_memory_backend_delete(memory_backend: MemoryBackend):
    key = "test_key"
    value = ETagContent(
        etag="test_etag",
        content={
            "response": b"test_value",
            "media_type": "application/json",
        },
    )
    ttl = 60

    await memory_backend.set(key, value, ttl)
    await memory_backend.delete(key)
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value is None


@pytest.mark.asyncio
async def test_memory_backend_clear(memory_backend: MemoryBackend):
    key1 = "test_key1"
    value1 = ETagContent(
        etag="test_etag1",
        content={
            "response": b"test_value1",
            "media_type": "application/json",
        },
    )
    key2 = "test_key2"
    value2 = ETagContent(
        etag="test_etag2",
        content={
            "response": b"test_value2",
            "media_type": "application/json",
        },
    )
    ttl = 60

    await memory_backend.set(key1, value1, ttl)
    await memory_backend.set(key2, value2, ttl)
    await memory_backend.clear()

    retrieved_value1 = await memory_backend.get(key1)
    retrieved_value2 = await memory_backend.get(key2)

    assert retrieved_value1 is None
    assert retrieved_value2 is None


@pytest.mark.asyncio
async def test_memory_backend_ttl_expiry(memory_backend: MemoryBackend):
    key = "test_key"
    value = ETagContent(
        etag="test_etag",
        content={
            "response": b"test_value",
            "media_type": "application/json",
        },
    )
    ttl = 1

    await memory_backend.set(key, value, ttl)
    await asyncio.sleep(2)  # Wait for the TTL to expire
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value is None


@pytest.mark.asyncio
async def test_memory_backend_cleanup(memory_backend: MemoryBackend):
    key1 = "test_key1"
    value1 = ETagContent(
        etag="test_etag1",
        content={
            "response": b"test_value1",
            "media_type": "application/json",
        },
    )
    ttl1 = 1
    key2 = "test_key2"
    value2 = ETagContent(
        etag="test_etag2",
        content={
            "response": b"test_value2",
            "media_type": "application/json",
        },
    )
    ttl2 = 60

    await memory_backend.set(key1, value1, ttl1)
    await memory_backend.set(key2, value2, ttl2)
    await asyncio.sleep(2)  # Wait for the TTL of key1 to expire
    await memory_backend.cleanup()

    retrieved_value1 = await memory_backend.get(key1)
    retrieved_value2 = await memory_backend.get(key2)

    assert retrieved_value1 is None
    assert retrieved_value2 == value2


@pytest.mark.asyncio
async def test_memory_backend_start_cleanup(memory_backend: MemoryBackend):
    memory_backend.start_cleanup()
    assert memory_backend._cleanup_task is not None
    assert not memory_backend._cleanup_task.done()
    memory_backend.stop_cleanup()  # Clean up after test


@pytest.mark.asyncio
async def test_memory_backend_stop_cleanup(memory_backend: MemoryBackend):
    memory_backend.start_cleanup()
    assert memory_backend._cleanup_task is not None
    memory_backend.stop_cleanup()
    assert memory_backend._cleanup_task is None


@pytest.mark.asyncio
async def test_memory_backend_double_start_cleanup(memory_backend: MemoryBackend):
    memory_backend.start_cleanup()
    original_task = memory_backend._cleanup_task
    memory_backend.start_cleanup()  # Start again
    assert memory_backend._cleanup_task is original_task  # Should be the same task
    memory_backend.stop_cleanup()  # Clean up after test


@pytest.mark.asyncio
async def test_memory_backend_stop_cleanup_when_not_running(
    memory_backend: MemoryBackend,
):
    memory_backend.stop_cleanup()  # Should not raise any error
    assert memory_backend._cleanup_task is None


@pytest.mark.asyncio
async def test_memory_backend_cleanup_task_impl(memory_backend: MemoryBackend):
    """Test that the cleanup task actually runs and cleans up expired items."""
    key1 = "test_key1"
    value1 = ETagContent(
        etag="test_etag1",
        content=b"test_value1",
    )
    key2 = "test_key2"
    value2 = ETagContent(
        etag="test_etag2",
        content=b"test_value2",
    )

    # Set shorter cleanup interval for testing
    memory_backend.cleanup_interval = 1

    # Set items with different TTLs
    await memory_backend.set(key1, value1, ttl=1)  # This should expire
    await memory_backend.set(key2, value2, ttl=60)  # This should remain

    # Start the cleanup task
    memory_backend.start_cleanup()

    # Wait for cleanup to run at least once
    await asyncio.sleep(2)

    # Get values after cleanup
    value1_after = await memory_backend.get(key1)
    value2_after = await memory_backend.get(key2)

    # Stop the cleanup task
    memory_backend.stop_cleanup()

    # Assert that expired item was cleaned up
    assert value1_after is None
    assert value2_after == value2
