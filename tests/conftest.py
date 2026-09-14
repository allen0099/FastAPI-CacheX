import pytest
import pytest_asyncio

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.manager_proxy import CacheManagerProxy
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.session.proxy import SessionManagerProxy
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
