import asyncio
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import cache
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.exceptions import ProxyNotSetError
from fastapi_cachex.manager_proxy import CacheManagerProxy
from fastapi_cachex.proxy import ProxyBase
from fastapi_cachex.proxy import get_backend_or_fallback
from fastapi_cachex.session.proxy import SessionManagerProxy
from fastapi_cachex.state.proxy import StateManagerProxy
from fastapi_cachex.types import CacheEntry

app = FastAPI()
client = TestClient(app)


@pytest_asyncio.fixture(autouse=True)
async def cleanup():
    # Reset backend before each test
    try:
        backend = BackendProxy.get()
        if isinstance(backend, MemoryBackend):
            backend.stop_cleanup()
        # Reset backend by setting it to None
        BackendProxy.set(None)
    except BackendNotFoundError:
        pass

    yield

    # Clean up after each test
    try:
        backend = BackendProxy.get()
        if isinstance(backend, MemoryBackend):
            await backend.clear()  # Clear all cached data
            backend.stop_cleanup()
        # Reset backend by setting it to None
        BackendProxy.set(None)
    except BackendNotFoundError:
        pass


def test_backend_switching():
    # Initial state should have no backend
    with pytest.raises(BackendNotFoundError):
        BackendProxy.get()

    # Set up MemoryBackend
    memory_backend = MemoryBackend()
    BackendProxy.set(memory_backend)
    assert isinstance(BackendProxy.get(), MemoryBackend)


def test_memory_cache():
    @app.get("/test")
    @cache(ttl=60)
    async def test_endpoint():
        return JSONResponse(content={"message": "test"})

    # Use MemoryBackend
    memory_backend = MemoryBackend()
    BackendProxy.set(memory_backend)

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
    BackendProxy.set(memory_backend)

    # Verify initial state
    assert memory_backend._cleanup_task is None

    # Set test data
    test_value = CacheEntry(fingerprint="test-etag", content=b"test_value")
    await memory_backend.set("test_key", test_value, ttl=1)

    # Verify data is stored correctly
    cached_value = await memory_backend.get("test_key")
    assert cached_value is not None
    assert cached_value.content == b"test_value"

    # Wait for data to expire (1 second + extra time)
    await asyncio.sleep(1.1)

    # Execute cleanup
    await memory_backend.cleanup()

    # Checked on the dictionary, not through `get`: `get` drops an expired
    # entry itself, so reading it back would pass even if `cleanup` did
    # nothing at all.
    assert "test_key" not in memory_backend.cache


def test_backend_proxy_cannot_be_instantiated():
    """Test that BackendProxy cannot be instantiated due to ProxyMeta."""
    with pytest.raises(TypeError, match="Proxy class cannot be instantiated"):
        BackendProxy()


def test_get_backend_alias_warns_and_delegates():
    """The 0.3.0 deprecation shims stay callable until 0.4.0 removes them."""
    backend = MemoryBackend()
    BackendProxy.set(backend)

    with pytest.warns(DeprecationWarning, match="get_backend\\(\\) is deprecated"):
        assert BackendProxy.get_backend() is backend


def test_set_backend_alias_warns_and_delegates():
    """Same for the setter, including clearing with `None`."""
    backend = MemoryBackend()

    with pytest.warns(DeprecationWarning, match="set_backend\\(\\) is deprecated"):
        BackendProxy.set_backend(backend)

    assert BackendProxy.get() is backend

    with pytest.warns(DeprecationWarning, match="set_backend\\(\\) is deprecated"):
        BackendProxy.set_backend(None)

    with pytest.raises(BackendNotFoundError):
        BackendProxy.get()


@pytest.mark.parametrize(
    "proxy", [CacheManagerProxy, SessionManagerProxy, StateManagerProxy]
)
def test_manager_proxies_raise_proxy_not_set_error(proxy) -> None:
    """An unset manager proxy is not a missing backend (#161).

    `ProxyNotSetError` subclasses `BackendNotFoundError`, which these proxies
    raised before, so existing handlers keep catching it.
    """
    previous = proxy._instance
    proxy.set(None)
    try:
        with pytest.raises(ProxyNotSetError, match=proxy.__name__) as exc_info:
            proxy.get()
        assert isinstance(exc_info.value, BackendNotFoundError)
    finally:
        proxy.set(previous)


def test_backend_proxy_still_raises_backend_not_found_error() -> None:
    BackendProxy.set(None)
    with pytest.raises(BackendNotFoundError) as exc_info:
        BackendProxy.get()
    assert not isinstance(exc_info.value, ProxyNotSetError)


class _Thing:
    """A stand-in instance for the generic `get_or_create` tests."""


class _ThingProxy(ProxyBase[_Thing]):
    """A proxy of its own, so these tests leave the library's proxies alone."""


@pytest.fixture
def thing_proxy() -> Iterator[type[_ThingProxy]]:
    _ThingProxy.set(None)
    yield _ThingProxy
    _ThingProxy.set(None)


def test_get_or_create_returns_the_registered_instance(
    thing_proxy: type[_ThingProxy],
) -> None:
    existing = _Thing()
    thing_proxy.set(existing)

    def factory() -> _Thing:
        pytest.fail("factory must not run while an instance is registered")

    assert thing_proxy.get_or_create(factory) is existing


def test_get_or_create_concurrent_first_calls_run_the_factory_once(
    thing_proxy: type[_ThingProxy],
) -> None:
    """Racing first callers on worker threads all get one registered instance."""
    calls = 0

    def slow_factory() -> _Thing:
        nonlocal calls
        calls += 1
        time.sleep(0.05)  # widen the window between the check and the set
        return _Thing()

    workers = 8
    barrier = threading.Barrier(workers)

    def first_call(_: int) -> _Thing:
        barrier.wait()
        return thing_proxy.get_or_create(slow_factory)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        things = list(pool.map(first_call, range(workers)))

    assert calls == 1
    assert len({id(thing) for thing in things}) == 1
    assert thing_proxy.get() is things[0]


def test_get_or_create_registers_nothing_when_the_factory_raises(
    thing_proxy: type[_ThingProxy],
) -> None:
    def failing_factory() -> _Thing:
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        thing_proxy.get_or_create(failing_factory)
    with pytest.raises(ProxyNotSetError):
        thing_proxy.get()
    # The lock was released: the next caller can still create one.
    assert isinstance(thing_proxy.get_or_create(_Thing), _Thing)


def test_get_or_create_factory_may_use_another_proxy(
    thing_proxy: type[_ThingProxy],
) -> None:
    """Each proxy class has its own lock, so nested creation cannot deadlock.

    The default `CacheManager` is built inside `CacheManagerProxy`'s lock and
    needs `BackendProxy.get_or_create` for its backend. Run in a thread with a
    timeout so a shared lock fails the test instead of hanging the suite.
    """
    BackendProxy.set(None)
    result: list[_Thing] = []

    def factory() -> _Thing:
        get_backend_or_fallback()
        return _Thing()

    worker = threading.Thread(
        target=lambda: result.append(thing_proxy.get_or_create(factory)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive(), "nested get_or_create deadlocked"
    assert result == [thing_proxy.get()]
    assert isinstance(BackendProxy.get(), MemoryBackend)
