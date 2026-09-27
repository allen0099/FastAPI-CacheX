import asyncio
import time
from collections.abc import AsyncGenerator
from datetime import datetime
from datetime import tzinfo
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends import memory
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.backends.redis import AsyncRedisCacheBackend
from fastapi_cachex.manager_proxy import CacheManagerProxy
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.session import manager as session_manager
from fastapi_cachex.session import models as session_models
from fastapi_cachex.session.proxy import SessionManagerProxy
from fastapi_cachex.state import manager as state_manager
from fastapi_cachex.state import models as state_models
from fastapi_cachex.state.proxy import StateManagerProxy

# Every proxy is a process-wide singleton, so whatever one test installs is
# still installed for the next one.
_PROXIES = (CacheManagerProxy, SessionManagerProxy, StateManagerProxy)


@pytest_asyncio.fixture
async def memory_backend():
    backend = MemoryBackend()
    backend.start_cleanup()  # Start cleanup task
    yield backend
    backend.stop_cleanup()  # Stop cleanup task


@pytest.fixture(autouse=True)
def setup_default_backend():
    """Auto-use fixture to set MemoryBackend as default for all tests."""
    backend = MemoryBackend()
    backend.start_cleanup()
    BackendProxy.set(backend)
    yield
    backend.stop_cleanup()


@pytest.fixture(autouse=True)
async def close_network_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[None, None]:
    """Close every Redis and Memcached client a test opens, when it ends.

    A client a test drops without `aclose()` keeps its sockets until the
    garbage collector finds it, often during a later test. The
    `ResourceWarning` then fails whichever test that is.
    Recording each backend as it is built and closing it here keeps every
    socket inside the test that opened it.

    `__new__` records rather than `__init__`, so the constructor's warnings
    still point at the test that called it.

    Clients are recognised by the module of their type rather than with
    `isinstance`, so this file imports neither optional client library, and
    a mock a test swapped in is left alone.
    """
    opened: list[AsyncRedisCacheBackend | MemcachedBackend] = []

    def record(cls: type[Any], *args: Any, **kwargs: Any) -> Any:
        backend = object.__new__(cls)
        opened.append(backend)
        return backend

    for cls in (AsyncRedisCacheBackend, MemcachedBackend):
        monkeypatch.setattr(cls, "__new__", record)
    yield
    for backend in opened:
        # Missing if the constructor raised; a test may swap in a mock.
        module = type(getattr(backend, "client", None)).__module__
        if module.startswith(("redis.", "pymemcache.")):
            await backend.aclose()


@pytest.fixture(autouse=True)
def reset_proxy_singletons():
    """Clear the remaining proxy singletons around every test.

    `BackendProxy` has `setup_default_backend`; the other three had nothing,
    so a test that failed before reaching its own cleanup left its manager
    installed for every test that ran afterwards.
    """
    for proxy in _PROXIES:
        proxy.set(None)
    yield
    for proxy in _PROXIES:
        proxy.set(None)


class Clock:
    """The wall clock the library reads, moved by hand instead of by sleeping."""

    def __init__(self) -> None:
        self.now = time.time()

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    async def wait(self, seconds: float, backend: BaseCacheBackend) -> None:
        """Let `seconds` pass as far as `backend` can tell.

        A live server expires keys on its own clock, which this one cannot
        move, so for anything but the memory backend the time really passes.
        """
        self.advance(seconds)
        if not isinstance(backend, MemoryBackend):
            await asyncio.sleep(seconds)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Point every time read behind a TTL or expiry check at a `Clock`.

    That is `time.time()` in the memory backend and `datetime.now()` in the
    session and state modules. PyJWT and live servers keep real time.
    """
    clock = Clock()

    class ClockDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
            return datetime.fromtimestamp(clock.now, tz)

    monkeypatch.setattr(memory, "time", SimpleNamespace(time=clock.time))
    for module in (session_manager, session_models, state_manager, state_models):
        monkeypatch.setattr(module, "datetime", ClockDatetime)
    return clock
