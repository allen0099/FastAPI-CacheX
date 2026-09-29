"""The implicit `MemoryBackend` fallback logs a warning once per process (#327)."""

import contextlib
import logging
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import fastapi_cachex.proxy
from fastapi_cachex import AppCache
from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheBackend
from fastapi_cachex import cache
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.dependencies import get_app_cache
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.proxy import get_backend_or_fallback

LOGGER = "fastapi_cachex.proxy"

app = FastAPI()


@app.get("/cached")
@cache(ttl=60)
async def cached_endpoint() -> dict[str, str]:
    return {"hello": "world"}


@app.get("/app-cache")
async def app_cache_endpoint(app_cache: AppCache) -> dict[str, str]:
    return {"backend": type(app_cache.backend).__name__}


@app.get("/cache-backend")
async def cache_backend_endpoint(backend: CacheBackend) -> dict[str, str]:
    return {"backend": type(backend).__name__}


client = TestClient(app)


def _fallback_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.levelno == logging.WARNING
    ]


@pytest.fixture
def no_backend(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Start with no backend and capture the proxy's warnings."""
    BackendProxy.set(None)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    yield
    with contextlib.suppress(BackendNotFoundError):
        backend = BackendProxy.get()
        if isinstance(backend, MemoryBackend):
            backend.stop_cleanup()


@pytest.mark.usefixtures("no_backend")
@pytest.mark.parametrize("path", ["/cached", "/app-cache", "/cache-backend"])
def test_implicit_fallback_warns_once(
    caplog: pytest.LogCaptureFixture, path: str
) -> None:
    for _ in range(3):
        assert client.get(path).status_code == 200
    get_backend_or_fallback()
    get_app_cache()

    assert isinstance(BackendProxy.get(), MemoryBackend)
    assert len(_fallback_warnings(caplog)) == 1


@pytest.mark.usefixtures("no_backend")
def test_implicit_fallback_warning_names_the_fix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    get_backend_or_fallback()

    [message] = _fallback_warnings(caplog)
    assert "BackendProxy.set(" in message
    assert "per process" in message
    assert "multiple workers" in message


@pytest.mark.usefixtures("no_backend")
def test_concurrent_first_calls_warn_once(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Racing first callers on worker threads register, and warn, only once."""

    class SlowMemoryBackend(MemoryBackend):
        def __init__(self) -> None:
            time.sleep(0.05)  # widen the window between the check and the set
            super().__init__()

    monkeypatch.setattr(fastapi_cachex.proxy, "MemoryBackend", SlowMemoryBackend)
    workers = 8
    barrier = threading.Barrier(workers)

    def first_call(index: int) -> object:
        barrier.wait()
        # Half through `AppCache`'s dependency, half through the bare helper.
        if index % 2:
            return get_app_cache().backend
        return get_backend_or_fallback()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        backends = list(pool.map(first_call, range(workers)))

    assert len({id(backend) for backend in backends}) == 1
    assert len(_fallback_warnings(caplog)) == 1


@pytest.mark.usefixtures("no_backend")
def test_fallback_warns_again_after_the_proxy_is_reset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Resetting to `None` makes the next call register a new fallback.

    That is a second registration, so it warns again; only tests reset the
    proxy this way.
    """
    first = get_backend_or_fallback()
    assert isinstance(first, MemoryBackend)
    BackendProxy.set(None)
    get_backend_or_fallback()

    assert len(_fallback_warnings(caplog)) == 2


@pytest.mark.parametrize("path", ["/cached", "/app-cache", "/cache-backend"])
def test_explicit_memory_backend_does_not_warn(
    caplog: pytest.LogCaptureFixture, path: str
) -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    assert client.get(path).status_code == 200
    get_backend_or_fallback()

    assert BackendProxy.get() is backend
    assert _fallback_warnings(caplog) == []
    backend.stop_cleanup()
