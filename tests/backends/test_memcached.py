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
    except (OSError, ConnectionRefusedError):
        return False
    else:
        return True
    finally:
        sock.close()


requires_memcached = pytest.mark.skipif(
    not is_memcached_running(),
    reason="Memcached server is not running",
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
            msg = "No module named 'pymemcache'"
            raise ImportError(msg)
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


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear_path(memcached_backend: MemcachedBackend):
    # Set up test data
    path = "/test"
    value = ETagContent(etag="test_etag", content=b"test_value")

    # Store data directly at the path
    await memcached_backend.set(path, value)

    # Test clearing the exact path
    cleared = await memcached_backend.clear_path(path, include_params=False)
    assert cleared == 1  # Should clear the exact path match

    # Verify the path is cleared
    result = await memcached_backend.get(path)
    assert result is None

    # Test include_params=True (should return 0 as this is not supported)
    cleared = await memcached_backend.clear_path(path, include_params=True)
    assert cleared == 0  # Should return 0 as this operation is not supported


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear_path_not_match(memcached_backend: MemcachedBackend):
    # Set up test data
    path = "/test"
    value = ETagContent(etag="test_etag", content=b"test_value")

    # Store data directly at the path
    await memcached_backend.set(path, value)

    # Make sure there is no data at a different path
    other_path = "/other_path"
    other_value = await memcached_backend.get(other_path)  # This should return None
    assert other_value is None

    # Test clearing a non-matching path
    cleared = await memcached_backend.clear_path(other_path, include_params=False)
    assert cleared == 0  # Should return 0 as the path does not match


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear_pattern(memcached_backend: MemcachedBackend):
    # Set up test data
    path = "/users/123"
    value = ETagContent(etag="test_etag", content=b"test_value")

    # Store some test data
    await memcached_backend.set(path, value)

    # Test pattern clearing (should always return 0 as not supported)
    cleared = await memcached_backend.clear_pattern("/users/*")
    assert cleared == 0  # Should return 0 as pattern matching is not supported

    # Verify the original data still exists (as pattern matching is not supported)
    result = await memcached_backend.get(path)
    assert result is not None
    assert result.etag == value.etag


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear_path_warning(memcached_backend: MemcachedBackend):
    # Test that warning is raised when using include_params=True
    with pytest.warns(
        RuntimeWarning,
        match="Memcached backend does not support pattern-based key clearing",
    ):
        cleared = await memcached_backend.clear_path("/test", include_params=True)
    assert cleared == 0


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear_pattern_warning(memcached_backend: MemcachedBackend):
    # Test that warning is raised when using pattern matching
    with pytest.warns(
        RuntimeWarning,
        match="Memcached backend does not support pattern matching",
    ):
        cleared = await memcached_backend.clear_pattern("/users/*")
    assert cleared == 0


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_content_str(monkeypatch) -> None:
    """Test the ETagContent content str branch in set()."""
    backend = MemcachedBackend(servers=["127.0.0.1:11211"])
    await backend.clear()
    value = ETagContent(etag="c", content="str-content")

    await backend.set("str-key", value)
    out = await backend.get("str-key")
    # Backend converts string content to bytes when deserializing
    assert out is not None
    assert out.etag == value.etag
    assert out.content == b"str-content"


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_uses_standard_json_branch(monkeypatch) -> None:
    """Force standard json to cover non-bytes serialization branch in set()."""
    # Swap out orjson with stdlib json inside module to return str from dumps
    import json as std_json
    import types
    from typing import cast

    from fastapi_cachex.backends import memcached as memcached_module

    original_json = cast("object", memcached_module.json)  # type: ignore[attr-defined]
    memcached_module.json = types.SimpleNamespace(  # type: ignore[attr-defined, assignment]
        dumps=std_json.dumps,
        loads=std_json.loads,
        JSONDecodeError=std_json.JSONDecodeError,
    )
    backend = MemcachedBackend(servers=["127.0.0.1:11211"])
    await backend.clear()
    try:
        key = "branch_json_key"
        value = ETagContent(etag="e", content=b"bytes-content")
        # When using std json, dumps returns str and code should encode to bytes
        await backend.set(key, value)
        got = await backend.get(key)
        assert got is not None
        assert got == value
    finally:
        memcached_module.json = original_json  # type: ignore[attr-defined, assignment]


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_get_invalid_and_missing_fields(
    memcached_backend: MemcachedBackend,
) -> None:
    """Cover get() error handling for invalid JSON and missing fields."""
    # Write invalid JSON directly into client
    raw_key = memcached_backend._make_key("invalid-json")
    memcached_backend.client.set(raw_key, b"not a json", expire=0)
    res = await memcached_backend.get("invalid-json")
    assert res is None

    # JSON missing required fields
    raw_key2 = memcached_backend._make_key("missing-fields")
    memcached_backend.client.set(raw_key2, b'{"some": "data"}', expire=0)
    res2 = await memcached_backend.get("missing-fields")
    assert res2 is None


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear_path_exception(
    monkeypatch,
    memcached_backend: MemcachedBackend,
) -> None:
    """Simulate client.delete raising to hit exception branch in clear_path()."""

    def boom(*args, **kwargs) -> None:
        msg = "delete failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(memcached_backend.client, "delete", boom)
    cleared = await memcached_backend.clear_path("/nope", include_params=False)
    assert cleared == 0
