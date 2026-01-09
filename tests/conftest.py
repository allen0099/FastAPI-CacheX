import pytest
import pytest_asyncio

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.proxy import BackendProxy


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
