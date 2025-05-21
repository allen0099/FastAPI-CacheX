import socket
import sys
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from fastapi_cachex.backends import AsyncValkeyCacheBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import ETagContent


def is_valkey_running(host: str = "127.0.0.1", port: int = 6379) -> bool:
    """Check if Valkey server is running."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect((host, port))
        s.close()
        return True
    except TimeoutError:
        return False


requires_valkey = pytest.mark.skipif(
    not is_valkey_running(), reason="Valkey server is not running"
)


@pytest_asyncio.fixture
async def async_valkey_backend() -> AsyncGenerator[AsyncValkeyCacheBackend, Any]:
    """Fixture for async Valkey cache backend."""
    if not is_valkey_running():
        pytest.skip("Valkey server is not running")

    backend = AsyncValkeyCacheBackend(
        host="127.0.0.1",
        port=6379,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
    )
    yield backend
    await backend.clear()


def test_valkey_without_valkey_package(monkeypatch):
    """Test that ValkeyBackend raises an error when valkey is not installed."""
    if "valkey" in sys.modules:
        del sys.modules["valkey"]

    orig_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "valkey":
            raise ImportError("No module named 'valkey'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    with pytest.raises(CacheXError) as exc_info:
        AsyncValkeyCacheBackend()
    assert "valkey is not installed" in str(exc_info.value)


@requires_valkey
@pytest.mark.asyncio
async def test_valkey_serialization_with_standard_json():
    """Test serialization with json module."""
    import json
    from unittest.mock import MagicMock

    # Create a mock json module with both dumps and loads
    mock_json = MagicMock()

    mock_json.dumps = json.dumps
    mock_json.loads = json.loads

    # Temporarily replace json module
    from fastapi_cachex.backends import valkey

    original_json = valkey.json  # type: ignore[attr-defined]
    valkey.json = mock_json  # type: ignore[attr-defined]

    try:
        backend = AsyncValkeyCacheBackend()
        value = ETagContent(etag="test-etag", content=b"test-content")
        serialized = backend._serialize(value)

        # Verify the serialization worked correctly
        assert isinstance(serialized, str)
        assert "test-etag" in serialized
        assert "test-content" in serialized

        # Ensure we can deserialize it back correctly
        deserialized = backend._deserialize(serialized)
        assert deserialized == value
    finally:
        # Restore original json module
        valkey.json = original_json  # type: ignore[attr-defined]


@pytest.mark.asyncio
class TestAsyncValkeyCacheBackend:
    @requires_valkey
    async def test_get_nonexistent(self, async_valkey_backend: AsyncValkeyCacheBackend):
        assert await async_valkey_backend.get("nonexistent") is None

    @requires_valkey
    async def test_set_get(self, async_valkey_backend: AsyncValkeyCacheBackend):
        value = ETagContent(etag="test-etag", content=b"test-content")
        await async_valkey_backend.set("test-key", value)
        result = await async_valkey_backend.get("test-key")
        assert result == value

    @requires_valkey
    async def test_set_with_ttl(self, async_valkey_backend: AsyncValkeyCacheBackend):
        value = ETagContent(etag="test-etag", content=b"test-content")
        await async_valkey_backend.set("test-key", value, ttl=100)
        ttl = await async_valkey_backend.client.ttl("test-key")
        assert ttl > 0 and ttl <= 100

    @requires_valkey
    async def test_delete(self, async_valkey_backend: AsyncValkeyCacheBackend):
        value = ETagContent(etag="test-etag", content=b"test-content")
        await async_valkey_backend.set("test-key", value)
        await async_valkey_backend.delete("test-key")
        assert await async_valkey_backend.get("test-key") is None

    @requires_valkey
    async def test_clear(self, async_valkey_backend: AsyncValkeyCacheBackend):
        value = ETagContent(etag="test-etag", content=b"test-content")
        await async_valkey_backend.set("test-key-1", value)
        await async_valkey_backend.set("test-key-2", value)
        await async_valkey_backend.clear()
        assert await async_valkey_backend.get("test-key-1") is None
        assert await async_valkey_backend.get("test-key-2") is None

    @requires_valkey
    async def test_clear_path(self, async_valkey_backend: AsyncValkeyCacheBackend):
        value = ETagContent(etag="test-etag", content=b"test-content")
        await async_valkey_backend.set("/users/1", value)
        await async_valkey_backend.set("/users/2", value)
        await async_valkey_backend.set("/posts/1", value)

        cleared = await async_valkey_backend.clear_path("/users/", include_params=True)
        assert cleared == 2
        assert await async_valkey_backend.get("/users/1") is None
        assert await async_valkey_backend.get("/users/2") is None
        assert await async_valkey_backend.get("/posts/1") == value

    @requires_valkey
    async def test_clear_pattern(self, async_valkey_backend: AsyncValkeyCacheBackend):
        value = ETagContent(etag="test-etag", content=b"test-content")
        await async_valkey_backend.set("/api/users/1", value)
        await async_valkey_backend.set("/api/users/2", value)
        await async_valkey_backend.set("/api/posts/1", value)

        cleared = await async_valkey_backend.clear_pattern("/api/users/*")
        assert cleared == 2
        assert await async_valkey_backend.get("/api/users/1") is None
        assert await async_valkey_backend.get("/api/users/2") is None
        assert await async_valkey_backend.get("/api/posts/1") == value


@requires_valkey
@pytest.mark.asyncio
async def test_valkey_ttl(async_valkey_backend: AsyncValkeyCacheBackend):
    """Test TTL functionality."""
    value = ETagContent(etag="test-etag", content=b"test-content")

    await async_valkey_backend.set("test-key", value, ttl=1)
    result = await async_valkey_backend.get("test-key")
    assert result is not None

    import asyncio

    await asyncio.sleep(1.5)  # Wait for TTL to expire

    result = await async_valkey_backend.get("test-key")
    assert result is None


@requires_valkey
@pytest.mark.asyncio
async def test_valkey_deserialize_invalid_json(
    async_valkey_backend: AsyncValkeyCacheBackend,
):
    """Test deserialize with invalid JSON data."""
    # Set invalid JSON directly using Valkey client
    await async_valkey_backend.client.set("invalid-json", "invalid json data")
    result = await async_valkey_backend.get("invalid-json")
    assert result is None

    # Set JSON without required fields
    await async_valkey_backend.client.set("missing-fields", '{"some": "data"}')
    result = await async_valkey_backend.get("missing-fields")
    assert result is None


@requires_valkey
@pytest.mark.asyncio
async def test_valkey_clear_path_no_matches(async_valkey_backend: AsyncValkeyCacheBackend):
    """Test clear_path when no keys match the pattern."""
    cleared = await async_valkey_backend.clear_path("/nonexistent/")
    assert cleared == 0


@requires_valkey
@pytest.mark.asyncio
async def test_valkey_clear_pattern_no_matches(
    async_valkey_backend: AsyncValkeyCacheBackend,
):
    """Test clear_pattern when no keys match the pattern."""
    cleared = await async_valkey_backend.clear_pattern("/nonexistent/*")
    assert cleared == 0
