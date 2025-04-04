import asyncio
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from fastapi_cachex.backends.memory import MemoryBackend

if TYPE_CHECKING:
    from fastapi_cachex.types import ETagContent


@pytest_asyncio.fixture
def memory_backend():
    return MemoryBackend()


@pytest.mark.asyncio
async def test_memory_backend_set_get(memory_backend: MemoryBackend):
    key = "test_key"
    value: ETagContent = {
        "response": b"test_value",
        "etag": "test_etag",
        "media_type": "application/json",
    }
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
    value: ETagContent = {
        "response": b"test_value",
        "etag": "test_etag",
        "media_type": "application/json",
    }
    ttl = 60

    await memory_backend.set(key, value, ttl)
    await memory_backend.delete(key)
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value is None


@pytest.mark.asyncio
async def test_memory_backend_clear(memory_backend: MemoryBackend):
    key1 = "test_key1"
    value1: ETagContent = {
        "response": b"test_value1",
        "etag": "test_etag1",
        "media_type": "application/json",
    }
    key2 = "test_key2"
    value2: ETagContent = {
        "response": b"test_value2",
        "etag": "test_etag2",
        "media_type": "application/json",
    }
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
    value: ETagContent = {
        "response": b"test_value",
        "etag": "test_etag",
        "media_type": "application/json",
    }
    ttl = 1

    await memory_backend.set(key, value, ttl)
    await asyncio.sleep(2)  # Wait for the TTL to expire
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value is None


@pytest.mark.asyncio
async def test_memory_backend_cleanup(memory_backend: MemoryBackend):
    key1 = "test_key1"
    value1: ETagContent = {
        "response": b"test_value1",
        "etag": "test_etag1",
        "media_type": "application/json",
    }
    ttl1 = 1
    key2 = "test_key2"
    value2: ETagContent = {
        "response": b"test_value2",
        "etag": "test_etag2",
        "media_type": "application/json",
    }
    ttl2 = 60

    await memory_backend.set(key1, value1, ttl1)
    await memory_backend.set(key2, value2, ttl2)
    await asyncio.sleep(2)  # Wait for the TTL of key1 to expire
    await memory_backend.cleanup()

    retrieved_value1 = await memory_backend.get(key1)
    retrieved_value2 = await memory_backend.get(key2)

    assert retrieved_value1 is None
    assert retrieved_value2 == value2
