import socket
import sys

import pytest
import pytest_asyncio

from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import ETagContent


def is_memcached_running(host: str = "127.0.0.1", port: int = 11211) -> bool:
    """Check if memcached is running."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
        return True
    except (OSError, ConnectionRefusedError):
        return False
    finally:
        sock.close()


requires_memcached = pytest.mark.skipif(
    not is_memcached_running(), reason="Memcached server is not running"
)


def test_memcached_without_pymemcache(monkeypatch):
    """Test that MemcachedBackend raises an error when pymemcache is not installed."""
    # Remove pymemcache from sys.modules
    if "pymemcache" in sys.modules:
        del sys.modules["pymemcache"]

    # Also patch __import__ to raise ImportError for pymemcache
    orig_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "pymemcache":
            raise ImportError("No module named 'pymemcache'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    with pytest.raises(CacheXError) as exc_info:
        MemcachedBackend(servers=["localhost:11211"])
    assert "pymemcache is not installed" in str(exc_info.value)


@pytest_asyncio.fixture
async def memcached_backend():
    backend = MemcachedBackend(servers=["127.0.0.1:11211"])
    await backend.clear()
    return backend


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_get(memcached_backend: MemcachedBackend):
    key = "test_key"
    value = ETagContent(etag="test_etag", content=b"test_content")
    ttl = 60

    await memcached_backend.set(key, value, ttl)
    retrieved_value = await memcached_backend.get(key)

    assert retrieved_value is not None
    assert retrieved_value.etag == value.etag
    assert retrieved_value.content == value.content


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_without_ttl(memcached_backend: MemcachedBackend):
    key = "test_key"
    value = ETagContent(etag="test_etag", content=b"test_content")

    await memcached_backend.set(key, value)
    retrieved_value = await memcached_backend.get(key)

    assert retrieved_value is not None
    assert retrieved_value.etag == value.etag
    assert retrieved_value.content == value.content


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_delete(memcached_backend: MemcachedBackend):
    key = "test_key"
    value = ETagContent(etag="test_etag", content=b"test_content")

    await memcached_backend.set(key, value)
    await memcached_backend.delete(key)
    retrieved_value = await memcached_backend.get(key)

    assert retrieved_value is None


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear(memcached_backend: MemcachedBackend):
    key1 = "test_key1"
    value1 = ETagContent(etag="test_etag1", content=b"test_content1")
    key2 = "test_key2"
    value2 = ETagContent(etag="test_etag2", content=b"test_content2")

    await memcached_backend.set(key1, value1)
    await memcached_backend.set(key2, value2)
    await memcached_backend.clear()

    retrieved_value1 = await memcached_backend.get(key1)
    retrieved_value2 = await memcached_backend.get(key2)

    assert retrieved_value1 is None
    assert retrieved_value2 is None
