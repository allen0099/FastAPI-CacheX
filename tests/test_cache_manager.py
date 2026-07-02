"""Tests for CacheManager application-level caching."""

import asyncio
import socket
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from typing import Any

import pytest
import pytest_asyncio

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.dependencies import get_app_cache
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.manager import CacheManager
from fastapi_cachex.manager_proxy import CacheManagerProxy
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

if TYPE_CHECKING:
    from fastapi_cachex.backends.base import BaseCacheBackend


def is_redis_running(host: str = "127.0.0.1", port: int = 6379) -> bool:
    """Check if Redis server is running."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect((host, port))
        s.close()
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False
    else:
        return True


def has_redis_package() -> bool:
    """Return True if the redis package is importable."""
    try:
        import redis.asyncio  # type: ignore[unused-ignore]  # noqa: F401

    except Exception:
        return False
    return True


@pytest_asyncio.fixture(
    params=[
        pytest.param("memory", id="MemoryBackend"),
        pytest.param(
            "redis",
            id="RedisBackend",
            marks=pytest.mark.skipif(
                not (is_redis_running() and has_redis_package()),
                reason="Redis not available",
            ),
        ),
    ]
)
async def cache_manager(request: Any) -> AsyncGenerator[CacheManager, Any]:
    """Create a CacheManager instance with different backends.

    Parametrized to run against MemoryBackend (always) and RedisBackend
    (only when Redis is available).
    """
    backend_type = request.param

    if backend_type == "memory":
        mem_backend = MemoryBackend()
        mem_backend.start_cleanup()
        backend: BaseCacheBackend = mem_backend
    else:  # redis
        from fastapi_cachex.backends import AsyncRedisCacheBackend

        backend = AsyncRedisCacheBackend(
            host="127.0.0.1",
            port=6379,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            key_prefix="test_cache_manager:",
        )

    BackendProxy.set(backend)
    manager = CacheManager()

    yield manager

    await backend.clear()
    if backend_type == "memory" and isinstance(backend, MemoryBackend):
        backend.stop_cleanup()


# --- Round-trip serialization -------------------------------------------------


@pytest.mark.asyncio
async def test_set_get_roundtrip_dict(cache_manager: CacheManager) -> None:
    """A dict value round-trips through set/get."""
    value = {"a": 1, "b": [1, 2, 3]}
    await cache_manager.set("key", value)
    assert await cache_manager.get("key") == value


@pytest.mark.asyncio
async def test_set_get_roundtrip_list(cache_manager: CacheManager) -> None:
    """A list value round-trips through set/get."""
    value = [1, "two", 3.0, None]
    await cache_manager.set("key", value)
    assert await cache_manager.get("key") == value


@pytest.mark.asyncio
async def test_set_get_roundtrip_str(cache_manager: CacheManager) -> None:
    """A plain string value round-trips through set/get."""
    await cache_manager.set("key", "hello")
    assert await cache_manager.get("key") == "hello"


@pytest.mark.asyncio
async def test_set_get_roundtrip_int(cache_manager: CacheManager) -> None:
    """An int value round-trips through set/get."""
    await cache_manager.set("key", 42)
    assert await cache_manager.get("key") == 42


@pytest.mark.asyncio
async def test_set_get_roundtrip_bool(cache_manager: CacheManager) -> None:
    """Bool values round-trip through set/get without collapsing to 0/1."""
    await cache_manager.set("key_true", value=True)
    await cache_manager.set("key_false", value=False)
    assert await cache_manager.get("key_true") is True
    assert await cache_manager.get("key_false") is False


@pytest.mark.asyncio
async def test_set_get_roundtrip_none_value(cache_manager: CacheManager) -> None:
    """Explicitly caching None as a value is distinguishable from a cache miss."""
    await cache_manager.set("key", None)
    assert await cache_manager.has("key") is True
    assert await cache_manager.get("key", default="MISSING") is None


# --- Missing keys / defaults ---------------------------------------------------


@pytest.mark.asyncio
async def test_get_missing_key_returns_default(cache_manager: CacheManager) -> None:
    """get() on a missing key returns None by default."""
    assert await cache_manager.get("nope") is None


@pytest.mark.asyncio
async def test_get_missing_key_returns_custom_default(
    cache_manager: CacheManager,
) -> None:
    """get() on a missing key returns the provided default."""
    assert await cache_manager.get("nope", default="fallback") == "fallback"


# --- delete / has ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_existing_key_returns_true(cache_manager: CacheManager) -> None:
    """delete() returns True when the key existed."""
    await cache_manager.set("key", "value")
    assert await cache_manager.delete("key") is True
    assert await cache_manager.get("key") is None


@pytest.mark.asyncio
async def test_delete_nonexistent_key_returns_false(
    cache_manager: CacheManager,
) -> None:
    """delete() returns False when the key never existed."""
    assert await cache_manager.delete("nope") is False


@pytest.mark.asyncio
async def test_has_existing_key_true(cache_manager: CacheManager) -> None:
    """has() returns True for an existing key."""
    await cache_manager.set("key", "value")
    assert await cache_manager.has("key") is True


@pytest.mark.asyncio
async def test_has_missing_key_false(cache_manager: CacheManager) -> None:
    """has() returns False for a missing key."""
    assert await cache_manager.has("nope") is False


# --- TTL --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ttl_expiry(cache_manager: CacheManager) -> None:
    """A value set with a short ttl expires and is no longer retrievable."""
    await cache_manager.set("key", "value", ttl=1)
    assert await cache_manager.get("key") == "value"

    await asyncio.sleep(1.2)

    assert await cache_manager.get("key") is None


@pytest.mark.asyncio
async def test_default_ttl_used_when_not_specified(
    memory_backend: MemoryBackend,
) -> None:
    """set() without an explicit ttl uses the manager's default_ttl."""
    BackendProxy.set(memory_backend)
    manager = CacheManager(default_ttl=1)

    await manager.set("key", "value")
    assert await manager.get("key") == "value"

    await asyncio.sleep(1.2)

    assert await manager.get("key") is None


@pytest.mark.asyncio
async def test_explicit_ttl_overrides_default_ttl(
    memory_backend: MemoryBackend,
) -> None:
    """An explicit ttl on set() overrides a long default_ttl."""
    BackendProxy.set(memory_backend)
    manager = CacheManager(default_ttl=3600)

    await manager.set("key", "value", ttl=1)
    await asyncio.sleep(1.2)

    assert await manager.get("key") is None


# --- Key prefixing ------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_key_prefix_is_cache_colon() -> None:
    """The default key_prefix is 'cache:'."""
    assert CacheManager().key_prefix == "cache:"


@pytest.mark.asyncio
async def test_key_prefix_isolation(memory_backend: MemoryBackend) -> None:
    """Two managers with different key_prefix values don't see each other's keys."""
    manager_a = CacheManager(backend=memory_backend, key_prefix="a:")
    manager_b = CacheManager(backend=memory_backend, key_prefix="b:")

    await manager_a.set("key", "from_a")

    assert await manager_a.get("key") == "from_a"
    assert await manager_b.get("key") is None


# --- clear / clear_prefix ------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_prefix_removes_only_matching_keys(
    memory_backend: MemoryBackend,
) -> None:
    """clear_prefix() removes manager keys but leaves unrelated backend keys."""
    manager = CacheManager(backend=memory_backend, key_prefix="cache:")
    await manager.set("a", 1)
    await manager.set("b", 2)

    unrelated_entry = CacheEntry(fingerprint="x", content=b'"unrelated"')
    await memory_backend.set("unrelated:key", unrelated_entry, ttl=None)

    removed = await manager.clear_prefix()

    assert removed == 2
    assert await manager.get("a") is None
    assert await manager.get("b") is None
    assert await memory_backend.get("unrelated:key") is not None


@pytest.mark.asyncio
async def test_clear_prefix_with_subprefix_argument(
    memory_backend: MemoryBackend,
) -> None:
    """clear_prefix(prefix) only clears keys under that sub-namespace."""
    manager = CacheManager(backend=memory_backend, key_prefix="cache:")
    await manager.set("users:1", "alice")
    await manager.set("users:2", "bob")
    await manager.set("other:1", "carol")

    removed = await manager.clear_prefix("users:")

    assert removed == 2
    assert await manager.get("users:1") is None
    assert await manager.get("users:2") is None
    assert await manager.get("other:1") == "carol"


@pytest.mark.asyncio
async def test_clear_removes_all_manager_keys(memory_backend: MemoryBackend) -> None:
    """clear() wipes all keys under this manager's own namespace only."""
    manager = CacheManager(backend=memory_backend, key_prefix="cache:")
    await manager.set("a", 1)
    await manager.set("b", 2)

    other_entry = CacheEntry(fingerprint="x", content=b'"other"')
    await memory_backend.set("oauth_state:untouched", other_entry, ttl=None)

    removed = await manager.clear()

    assert removed == 2
    assert await manager.get("a") is None
    assert await memory_backend.get("oauth_state:untouched") is not None


# --- Serialization errors ----------------------------------------------------


@pytest.mark.asyncio
async def test_set_non_json_serializable_raises_type_error(
    cache_manager: CacheManager,
) -> None:
    """set() propagates TypeError for a non-JSON-serializable value."""
    with pytest.raises(TypeError):
        await cache_manager.set("key", {1, 2, 3})


@pytest.mark.asyncio
async def test_get_with_corrupted_backend_content_returns_default(
    memory_backend: MemoryBackend,
) -> None:
    """get() returns the default when stored content isn't valid JSON."""
    manager = CacheManager(backend=memory_backend)
    cache_key = f"{manager.key_prefix}bad"
    entry = CacheEntry(fingerprint="x", content=b"not valid json")
    await memory_backend.set(cache_key, entry, ttl=60)

    assert await manager.get("bad", default="fallback") == "fallback"


@pytest.mark.asyncio
async def test_get_with_non_utf8_content_returns_default(
    memory_backend: MemoryBackend,
) -> None:
    """get() returns the default when stored content isn't valid UTF-8."""
    manager = CacheManager(backend=memory_backend)
    cache_key = f"{manager.key_prefix}bad"
    entry = CacheEntry(fingerprint="x", content=b"\xff\xfe non-utf8")
    await memory_backend.set(cache_key, entry, ttl=60)

    assert await manager.get("bad", default="fallback") == "fallback"


# --- Construction / backend resolution -----------------------------------------


@pytest.mark.asyncio
async def test_manager_accepts_explicit_backend() -> None:
    """CacheManager(backend=...) uses the provided backend without touching BackendProxy."""
    backend = MemoryBackend()
    backend.start_cleanup()

    try:
        BackendProxy.set(None)

        manager = CacheManager(backend=backend)
        assert manager.backend is backend

        await manager.set("key", "value")
        assert await manager.get("key") == "value"
    finally:
        backend.stop_cleanup()
        await backend.clear()


@pytest.mark.asyncio
async def test_manager_falls_back_to_backend_proxy(
    memory_backend: MemoryBackend,
) -> None:
    """CacheManager() with no backend argument uses BackendProxy.get()."""
    BackendProxy.set(memory_backend)
    proxy_backend = BackendProxy.get()

    manager = CacheManager()

    assert manager.backend is proxy_backend

    await manager.set("key", "value")
    assert await manager.get("key") == "value"


def test_manager_raises_when_no_backend_configured() -> None:
    """CacheManager() raises BackendNotFoundError if no backend is configured."""
    BackendProxy.set(None)
    try:
        with pytest.raises(BackendNotFoundError):
            CacheManager()
    finally:
        BackendProxy.set(MemoryBackend())


@pytest.mark.asyncio
async def test_multiple_managers_independent_key_prefixes_same_backend(
    memory_backend: MemoryBackend,
) -> None:
    """Two managers sharing one backend but different prefixes don't collide."""
    manager_a = CacheManager(backend=memory_backend, key_prefix="feature_a:")
    manager_b = CacheManager(backend=memory_backend, key_prefix="feature_b:")

    await manager_a.set("shared_name", "value_a")
    await manager_b.set("shared_name", "value_b")

    assert await manager_a.get("shared_name") == "value_a"
    assert await manager_b.get("shared_name") == "value_b"


# --- CacheManagerProxy ----------------------------------------------------------


def test_cache_manager_proxy_get_set(memory_backend: MemoryBackend) -> None:
    """CacheManagerProxy.set()/.get() round-trip a CacheManager instance."""
    manager = CacheManager(backend=memory_backend)
    CacheManagerProxy.set(manager)
    try:
        assert CacheManagerProxy.get() is manager
    finally:
        CacheManagerProxy.set(None)


def test_cache_manager_proxy_raises_when_unset() -> None:
    """CacheManagerProxy.get() raises BackendNotFoundError when unset."""
    CacheManagerProxy.set(None)
    with pytest.raises(BackendNotFoundError):
        CacheManagerProxy.get()


# --- get_app_cache dependency ---------------------------------------------------


def test_get_app_cache_lazily_creates_default(memory_backend: MemoryBackend) -> None:
    """get_app_cache() lazily creates and registers a default CacheManager."""
    BackendProxy.set(memory_backend)
    CacheManagerProxy.set(None)
    try:
        manager = get_app_cache()
        assert isinstance(manager, CacheManager)
        assert CacheManagerProxy.get() is manager
    finally:
        CacheManagerProxy.set(None)


def test_get_app_cache_reuses_existing_proxy_instance(
    memory_backend: MemoryBackend,
) -> None:
    """get_app_cache() reuses an already-set CacheManagerProxy instance."""
    existing = CacheManager(backend=memory_backend)
    CacheManagerProxy.set(existing)
    try:
        assert get_app_cache() is existing
    finally:
        CacheManagerProxy.set(None)
