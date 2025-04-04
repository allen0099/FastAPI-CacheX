import asyncio

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import cache
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.types import ETagContent

app = FastAPI()
client = TestClient(app)


@pytest_asyncio.fixture(autouse=True)
async def cleanup():
    # Reset backend before each test
    try:
        backend = BackendProxy.get_backend()
        if isinstance(backend, MemoryBackend):
            backend.stop_cleanup()
        # Reset backend by setting it to None
        BackendProxy.set_backend(None)
    except BackendNotFoundError:
        pass

    yield

    # Clean up after each test
    try:
        backend = BackendProxy.get_backend()
        if isinstance(backend, MemoryBackend):
            await backend.clear()  # Clear all cached data
            backend.stop_cleanup()
        # Reset backend by setting it to None
        BackendProxy.set_backend(None)
    except BackendNotFoundError:
        pass


def test_backend_switching():
    # Initial state should have no backend
    with pytest.raises(BackendNotFoundError):
        BackendProxy.get_backend()

    # Set up MemoryBackend
    memory_backend = MemoryBackend()
    BackendProxy.set_backend(memory_backend)
    assert isinstance(BackendProxy.get_backend(), MemoryBackend)


def test_memory_cache():
    @app.get("/test")
    @cache(ttl=60)
    async def test_endpoint():
        return JSONResponse(content={"message": "test"})

    # Use MemoryBackend
    memory_backend = MemoryBackend()
    BackendProxy.set_backend(memory_backend)

    # First request should return 200
    response1 = client.get("/test")
    assert response1.status_code == 200
    etag1 = response1.headers["ETag"]

    # Request with same ETag should return 304
    response2 = client.get("/test", headers={"If-None-Match": etag1})
    assert response2.status_code == 304


@pytest.mark.asyncio
async def test_backend_cleanup():
    # Run cleanup task in async environment
    memory_backend = MemoryBackend()
    BackendProxy.set_backend(memory_backend)

    # Verify initial state
    assert memory_backend._cleanup_task is None

    # Set test data
    test_value = ETagContent(etag="test-etag", content="test_value")
    await memory_backend.set("test_key", test_value, ttl=1)

    # Verify data is stored correctly
    cached_value = await memory_backend.get("test_key")
    assert cached_value is not None
    assert cached_value.content == "test_value"

    # Wait for data to expire (1 second + extra time)
    await asyncio.sleep(1.1)

    # Execute cleanup
    await memory_backend.cleanup()

    # Verify data has been cleaned up
    assert await memory_backend.get("test_key") is None
