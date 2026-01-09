import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheBackend
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.exceptions import BackendNotFoundError

# Setup FastAPI application
app = FastAPI()
client = TestClient(app)


# Define test endpoint (not a test function)
@app.get("/test-backend")
async def backend_endpoint(backend: CacheBackend):
    return {"backend_type": backend.__class__.__name__}


# Actual test functions
@pytest.mark.asyncio
async def test_get_cache_backend_no_backend():
    """Test that get_cache_backend raises BackendNotFoundError when no backend is set."""
    BackendProxy.set(None)
    with pytest.raises(BackendNotFoundError):
        client.get("/test-backend")


@pytest.mark.asyncio
async def test_get_cache_backend_with_memory_backend():
    """Test that get_cache_backend returns the configured backend."""
    backend = MemoryBackend()
    BackendProxy.set(backend)

    response = client.get("/test-backend")
    assert response.status_code == 200
    assert response.json() == {"backend_type": "MemoryBackend"}
