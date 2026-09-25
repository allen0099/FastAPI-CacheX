import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheBackend
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.dependencies import get_app_cache
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.manager import CacheManager
from fastapi_cachex.manager_proxy import CacheManagerProxy

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


@pytest.mark.asyncio
async def test_get_app_cache_falls_back_to_memory_without_a_backend():
    """`AppCache` must work on its own, like `@cache` already does.

    Building the manager without a backend raised `BackendNotFoundError` out of
    its constructor, so the dependency returned a 500 unless a `@cache` route
    happened to have installed the fallback backend first.
    """
    BackendProxy.set(None)

    manager = get_app_cache()

    assert isinstance(manager.backend, MemoryBackend)
    # The fallback is registered, so `@cache` and `AppCache` share one backend.
    assert BackendProxy.get() is manager.backend
    assert CacheManagerProxy.get() is manager


@pytest.mark.asyncio
async def test_get_app_cache_uses_the_configured_backend():
    """A configured backend must not be replaced by the fallback."""
    backend = MemoryBackend()
    BackendProxy.set(backend)

    manager = get_app_cache()

    assert manager.backend is backend


def test_get_app_cache_concurrent_first_calls_share_one_backend(monkeypatch):
    """Concurrent first calls must agree on one backend and one manager.

    FastAPI runs the sync `get_app_cache` in worker threads. Without a lock,
    two first requests each built a `MemoryBackend` and a `CacheManager`, and
    the later `set()` replaced the earlier, leaving two caches that could not
    see each other's entries.
    """
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    import fastapi_cachex.proxy

    class SlowMemoryBackend(MemoryBackend):
        def __init__(self) -> None:
            time.sleep(0.05)  # widen the window between the check and the set
            super().__init__()

    monkeypatch.setattr(fastapi_cachex.proxy, "MemoryBackend", SlowMemoryBackend)
    BackendProxy.set(None)
    CacheManagerProxy.set(None)

    workers = 8
    barrier = threading.Barrier(workers)

    def first_call() -> CacheManager:
        barrier.wait()
        return get_app_cache()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        managers = list(pool.map(lambda _: first_call(), range(workers)))

    assert len({id(m) for m in managers}) == 1
    assert len({id(m.backend) for m in managers}) == 1
    assert BackendProxy.get() is managers[0].backend
    assert CacheManagerProxy.get() is managers[0]
