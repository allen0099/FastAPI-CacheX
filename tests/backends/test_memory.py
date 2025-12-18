import asyncio
import time

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


@pytest.mark.asyncio
async def test_memory_backend_clear_path(memory_backend: MemoryBackend):
    # Set up test data with proper cache key format: method|||host|||path|||query_params
    # The split uses "|||", 3 which creates up to 4 parts
    path = "/test"
    value1 = ETagContent(etag="test_etag1", content=b"test_value1")
    value2 = ETagContent(etag="test_etag2", content=b"test_value2")
    value3 = ETagContent(etag="test_etag3", content=b"test_value3")

    # Store data with method|||host|||path format (3 parts minimum)
    await memory_backend.set(f"GET|||localhost|||{path}", value1)
    await memory_backend.set(f"POST|||localhost|||{path}", value2)
    await memory_backend.set("GET|||localhost|||/other", value3)

    # Test clearing without parameters - should clear entries with exact path
    cleared = await memory_backend.clear_path(path, include_params=False)
    assert cleared == 2  # Should clear GET and POST entries with /test path

    # Verify the other path's data still exists
    other_value = await memory_backend.get("GET|||localhost|||/other")
    assert other_value == value3


@pytest.mark.asyncio
async def test_memory_backend_clear_pattern(memory_backend: MemoryBackend):
    # Set up test data with proper cache key format: method|||host|||path|||query_params
    # The split uses "|||", 3 which creates up to 4 parts
    value1 = ETagContent(etag="test_etag1", content=b"test_value1")
    value2 = ETagContent(etag="test_etag2", content=b"test_value2")
    value3 = ETagContent(etag="test_etag3", content=b"test_value3")

    # Store data with method|||host|||path format
    await memory_backend.set("GET|||localhost|||/users/123", value1)
    await memory_backend.set("POST|||localhost|||/users/456", value2)
    await memory_backend.set("GET|||localhost|||/posts/789", value3)

    # Test clearing with pattern
    cleared = await memory_backend.clear_pattern("/users/*")
    assert cleared == 2  # Should clear both user entries

    # Verify the posts data still exists
    posts_value = await memory_backend.get("GET|||localhost|||/posts/789")
    assert posts_value == value3


@pytest.mark.asyncio
async def test_memory_backend_get_all_keys_empty(memory_backend: MemoryBackend):
    """Test get_all_keys returns empty list for empty cache."""
    keys = await memory_backend.get_all_keys()
    assert keys == []


@pytest.mark.asyncio
async def test_memory_backend_get_all_keys_with_entries(
    memory_backend: MemoryBackend,
) -> None:
    """Test get_all_keys returns all cache keys."""
    key1 = "GET|||localhost|||/users"
    key2 = "POST|||localhost|||/users"
    key3 = "GET|||localhost|||/posts"

    value = ETagContent(etag="test_etag", content=b"test_value")

    await memory_backend.set(key1, value)
    await memory_backend.set(key2, value)
    await memory_backend.set(key3, value)

    keys = await memory_backend.get_all_keys()
    assert sorted(keys) == sorted([key1, key2, key3])
    assert len(keys) == 3


@pytest.mark.asyncio
async def test_memory_backend_get_cache_data_empty(memory_backend: MemoryBackend):
    """Test get_cache_data returns empty dict for empty cache."""
    cache_data = await memory_backend.get_cache_data()
    assert cache_data == {}


@pytest.mark.asyncio
async def test_memory_backend_get_cache_data_with_entries(
    memory_backend: MemoryBackend,
) -> None:
    """Test get_cache_data returns all cache data with expiry."""
    key1 = "GET|||localhost|||/users"
    key2 = "POST|||localhost|||/users"
    value1 = ETagContent(etag="etag1", content=b"value1")
    value2 = ETagContent(etag="etag2", content=b"value2")

    # Set with TTL
    await memory_backend.set(key1, value1, ttl=3600)
    # Set without TTL
    await memory_backend.set(key2, value2)

    cache_data = await memory_backend.get_cache_data()

    assert len(cache_data) == 2
    assert key1 in cache_data
    assert key2 in cache_data

    # Verify values
    stored_value1, expiry1 = cache_data[key1]
    stored_value2, expiry2 = cache_data[key2]

    assert stored_value1 == value1
    assert stored_value2 == value2

    # Verify expiry (key1 should have expiry, key2 should not)
    assert expiry1 is not None
    assert expiry2 is None


@pytest.mark.asyncio
async def test_memory_backend_get_cache_data_expired_entries(
    memory_backend: MemoryBackend,
) -> None:
    """Test get_cache_data includes expired entries."""
    key = "GET|||localhost|||/test"
    value = ETagContent(etag="test_etag", content=b"test_value")

    # Set with very short TTL
    await memory_backend.set(key, value, ttl=1)

    # Wait for expiry
    await asyncio.sleep(1.1)

    cache_data = await memory_backend.get_cache_data()

    # Expired entries should still be in the raw cache data
    # but get() won't return them
    assert key in cache_data
    retrieved_value, expiry = cache_data[key]
    assert retrieved_value == value
    assert expiry is not None
    assert expiry <= time.time()
