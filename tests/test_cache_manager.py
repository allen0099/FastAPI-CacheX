"""Tests for CacheManager application-level caching."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial
from typing import Any

import pytest
import pytest_asyncio

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.dependencies import get_app_cache
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.exceptions import LockTimeoutError
from fastapi_cachex.lock import CacheLock
from fastapi_cachex.manager import CacheManager
from fastapi_cachex.manager_proxy import CacheManagerProxy
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import log_ref
from tests.conftest import Clock
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import flush_memcached
from tests.live_servers import requires_memcached
from tests.live_servers import requires_redis
from tests.live_servers import requires_redis_package


@pytest_asyncio.fixture(
    params=[
        pytest.param("memory", id="MemoryBackend"),
        pytest.param(
            "redis",
            id="RedisBackend",
            marks=[requires_redis, requires_redis_package],
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
            host=REDIS_HOST,
            port=REDIS_PORT,
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


async def test_set_get_roundtrip_dict(cache_manager: CacheManager) -> None:
    """A dict value round-trips through set/get."""
    value = {"a": 1, "b": [1, 2, 3]}
    await cache_manager.set("key", value)
    assert await cache_manager.get("key") == value


async def test_set_get_roundtrip_list(cache_manager: CacheManager) -> None:
    """A list value round-trips through set/get."""
    value = [1, "two", 3.0, None]
    await cache_manager.set("key", value)
    assert await cache_manager.get("key") == value


async def test_set_get_roundtrip_str(cache_manager: CacheManager) -> None:
    """A plain string value round-trips through set/get."""
    await cache_manager.set("key", "hello")
    assert await cache_manager.get("key") == "hello"


async def test_set_get_roundtrip_int(cache_manager: CacheManager) -> None:
    """An int value round-trips through set/get."""
    await cache_manager.set("key", 42)
    assert await cache_manager.get("key") == 42


async def test_set_get_roundtrip_bool(cache_manager: CacheManager) -> None:
    """Bool values round-trip through set/get without collapsing to 0/1."""
    await cache_manager.set("key_true", value=True)
    await cache_manager.set("key_false", value=False)
    assert await cache_manager.get("key_true") is True
    assert await cache_manager.get("key_false") is False


async def test_set_get_roundtrip_none_value(cache_manager: CacheManager) -> None:
    """Explicitly caching None as a value is distinguishable from a cache miss."""
    await cache_manager.set("key", None)
    assert await cache_manager.has("key") is True
    assert await cache_manager.get("key", default="MISSING") is None


# --- Missing keys / defaults ---------------------------------------------------


async def test_get_missing_key_returns_default(cache_manager: CacheManager) -> None:
    """get() on a missing key returns None by default."""
    assert await cache_manager.get("nope") is None


async def test_get_missing_key_returns_custom_default(
    cache_manager: CacheManager,
) -> None:
    """get() on a missing key returns the provided default."""
    assert await cache_manager.get("nope", default="fallback") == "fallback"


# --- get_or_set -----------------------------------------------------------------


async def test_get_or_set_hit_does_not_call_factory(
    cache_manager: CacheManager,
) -> None:
    """get_or_set() returns the cached value without invoking factory on a hit."""
    await cache_manager.set("key", "existing")
    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return "from_factory"

    result = await cache_manager.get_or_set("key", factory)

    assert result == "existing"
    assert calls == 0


async def test_get_or_set_miss_calls_sync_factory_and_caches(
    cache_manager: CacheManager,
) -> None:
    """get_or_set() calls a sync factory on a miss and caches the result."""
    calls = 0

    def factory() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"computed": 1}

    result = await cache_manager.get_or_set("nope", factory)

    assert result == {"computed": 1}
    assert calls == 1
    assert await cache_manager.get("nope") == {"computed": 1}


async def test_get_or_set_miss_calls_async_factory_and_caches(
    cache_manager: CacheManager,
) -> None:
    """get_or_set() calls an async factory on a miss and caches the result."""
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return "async_value"

    result = await cache_manager.get_or_set("nope", factory)

    assert result == "async_value"
    assert calls == 1
    assert await cache_manager.get("nope") == "async_value"


@pytest.mark.parametrize("wrap", ["lambda", "partial"])
async def test_get_or_set_awaits_a_sync_callable_returning_an_awaitable(
    cache_manager: CacheManager, wrap: str
) -> None:
    """The documented `lambda: load_user(42)` form must store the result, not the coroutine."""

    async def load_user(user_id: int) -> dict[str, int]:
        return {"id": user_id}

    factory = (lambda: load_user(42)) if wrap == "lambda" else partial(load_user, 42)

    result = await cache_manager.get_or_set("user", factory)

    assert result == {"id": 42}
    assert await cache_manager.get("user") == {"id": 42}


async def test_get_or_set_honors_ttl_on_created_value(
    cache_manager: CacheManager, clock: Clock
) -> None:
    """get_or_set() applies the given ttl to a newly created value."""
    result = await cache_manager.get_or_set("key", lambda: "value", ttl=1)
    assert result == "value"
    assert await cache_manager.get("key") == "value"

    await clock.wait(1.2, cache_manager.backend)

    assert await cache_manager.get("key") is None


async def test_get_or_set_treats_corrupted_content_as_miss(
    memory_backend: MemoryBackend,
) -> None:
    """get_or_set() calls factory when stored content can't be decoded."""
    manager = CacheManager(backend=memory_backend)
    cache_key = f"{manager.key_prefix}bad"
    entry = CacheEntry(fingerprint="x", content=b"not valid json")
    await memory_backend.set(cache_key, entry, ttl=60)

    result = await manager.get_or_set("bad", lambda: "repaired")

    assert result == "repaired"
    assert await manager.get("bad") == "repaired"


@pytest.mark.parametrize(
    ("value", "decoded"),
    [
        pytest.param((1, 2), [1, 2], id="tuple"),
        pytest.param({1: "a"}, {"1": "a"}, id="int-keyed-dict"),
        pytest.param(
            {"items": [(1, {2: (3,)})], "n": None},
            {"items": [[1, {"2": [3]}]], "n": None},
            id="nested",
        ),
    ],
)
async def test_get_or_set_miss_returns_the_value_a_hit_returns(
    cache_manager: CacheManager, value: Any, decoded: Any
) -> None:
    """get_or_set() returns the JSON round-tripped value on a miss, like a hit."""
    miss = await cache_manager.get_or_set("key", lambda: value)
    hit = await cache_manager.get_or_set("key", lambda: pytest.fail("factory ran"))

    assert miss == decoded
    assert hit == decoded
    assert type(miss) is type(hit)


async def test_get_or_set_miss_encodes_the_value_once(
    memory_backend: MemoryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_or_set() serialises the factory's value once and stores those bytes."""
    manager = CacheManager(backend=memory_backend)
    encoded: list[Any] = []
    original = CacheManager._encode

    def counting_encode(value: Any) -> CacheEntry:
        encoded.append(value)
        return original(value)

    monkeypatch.setattr(CacheManager, "_encode", staticmethod(counting_encode))

    result = await manager.get_or_set("key", lambda: (1, 2))

    assert encoded == [(1, 2)]
    assert result == [1, 2]
    stored = await memory_backend.get(f"{manager.key_prefix}key")
    assert stored is not None
    assert stored.content == b"[1, 2]"


async def test_get_or_set_non_json_serializable_raises_and_stores_nothing(
    cache_manager: CacheManager,
) -> None:
    """get_or_set() raises TypeError after factory runs and leaves the key free."""
    calls = 0

    def factory() -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(TypeError):
        await cache_manager.get_or_set("key", factory)

    assert calls == 1
    assert not await cache_manager.has("key")


async def test_decode_failure_warning_logs_a_digest_not_the_key(
    memory_backend: MemoryBackend, caplog: pytest.LogCaptureFixture
) -> None:
    """Keys often embed e-mails or user IDs: the full key is logged at DEBUG only."""
    manager = CacheManager(backend=memory_backend)
    key = "user:alice@example.com"
    entry = CacheEntry(fingerprint="x", content=b"not valid json")
    await memory_backend.set(f"{manager.key_prefix}{key}", entry, ttl=60)

    with caplog.at_level(logging.DEBUG, logger="fastapi_cachex.manager"):
        assert await manager.get(key) is None

    [warning] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "alice" not in warning
    assert f"key_ref={log_ref(key)}" in warning
    assert any(
        r.levelno == logging.DEBUG and f"key={key}" in r.getMessage()
        for r in caplog.records
    )


# --- add ------------------------------------------------------------------------


async def test_add_stores_when_key_is_free(cache_manager: CacheManager) -> None:
    """add() stores the value and reports it when nothing holds the key."""
    assert await cache_manager.add("event:1", {"sent": True}) is True
    assert await cache_manager.get("event:1") == {"sent": True}


async def test_add_keeps_the_existing_value(cache_manager: CacheManager) -> None:
    """add() never overwrites: the first value stays and the call reports False."""
    await cache_manager.set("event:1", "first")

    assert await cache_manager.add("event:1", "second") is False
    assert await cache_manager.get("event:1") == "first"


async def test_add_concurrent_callers_have_exactly_one_winner(
    cache_manager: CacheManager,
) -> None:
    """The check and the write are atomic, so only one concurrent add() succeeds."""
    results = await asyncio.gather(
        *(cache_manager.add("event:1", n) for n in range(20))
    )

    assert results.count(True) == 1
    # The stored value is the winner's, not a later caller's.
    assert await cache_manager.get("event:1") == results.index(True)


async def test_add_ttl_expires_the_claim(
    cache_manager: CacheManager, clock: Clock
) -> None:
    """An explicit ttl applies, and once it lapses the key can be added again."""
    assert await cache_manager.add("event:1", "first", ttl=1) is True
    assert await cache_manager.add("event:1", "second", ttl=1) is False

    await clock.wait(1.2, cache_manager.backend)

    assert await cache_manager.add("event:1", "third") is True
    assert await cache_manager.get("event:1") == "third"


async def test_add_uses_default_ttl(
    memory_backend: MemoryBackend, clock: Clock
) -> None:
    """add() without an explicit ttl falls back to the manager's default_ttl."""
    manager = CacheManager(backend=memory_backend, default_ttl=1)
    assert await manager.add("event:1", "value") is True

    clock.advance(1.2)

    assert await manager.get("event:1") is None


async def test_add_treats_undecodable_content_as_present(
    memory_backend: MemoryBackend,
) -> None:
    """Unlike get(), add() sees a corrupted entry as an existing key."""
    manager = CacheManager(backend=memory_backend)
    entry = CacheEntry(fingerprint="x", content=b"not valid json")
    await memory_backend.set(f"{manager.key_prefix}bad", entry, ttl=60)

    assert await manager.add("bad", "value") is False
    assert await manager.get("bad", default="fallback") == "fallback"


async def test_add_non_json_serializable_raises_type_error(
    cache_manager: CacheManager,
) -> None:
    """add() raises TypeError like set(), and leaves the key free."""
    with pytest.raises(TypeError):
        await cache_manager.add("event:1", {1, 2, 3})

    assert await cache_manager.has("event:1") is False


# --- delete / has ---------------------------------------------------------------


async def test_delete_existing_key_returns_true(cache_manager: CacheManager) -> None:
    """delete() returns True when the key existed."""
    await cache_manager.set("key", "value")
    assert await cache_manager.delete("key") is True
    assert await cache_manager.get("key") is None


async def test_delete_nonexistent_key_returns_false(
    cache_manager: CacheManager,
) -> None:
    """delete() returns False when the key never existed."""
    assert await cache_manager.delete("nope") is False


async def test_has_existing_key_true(cache_manager: CacheManager) -> None:
    """has() returns True for an existing key."""
    await cache_manager.set("key", "value")
    assert await cache_manager.has("key") is True


async def test_has_missing_key_false(cache_manager: CacheManager) -> None:
    """has() returns False for a missing key."""
    assert await cache_manager.has("nope") is False


# --- TTL --------------------------------------------------------------------------


async def test_ttl_expiry(cache_manager: CacheManager, clock: Clock) -> None:
    """A value set with a short ttl expires and is no longer retrievable."""
    await cache_manager.set("key", "value", ttl=1)
    assert await cache_manager.get("key") == "value"

    await clock.wait(1.2, cache_manager.backend)

    assert await cache_manager.get("key") is None


async def test_default_ttl_used_when_not_specified(
    memory_backend: MemoryBackend, clock: Clock
) -> None:
    """set() without an explicit ttl uses the manager's default_ttl."""
    BackendProxy.set(memory_backend)
    manager = CacheManager(default_ttl=1)

    await manager.set("key", "value")
    assert await manager.get("key") == "value"

    clock.advance(1.2)

    assert await manager.get("key") is None


async def test_explicit_ttl_overrides_default_ttl(
    memory_backend: MemoryBackend, clock: Clock
) -> None:
    """An explicit ttl on set() overrides a long default_ttl."""
    BackendProxy.set(memory_backend)
    manager = CacheManager(default_ttl=3600)

    await manager.set("key", "value", ttl=1)
    clock.advance(1.2)

    assert await manager.get("key") is None


# --- Key prefixing ------------------------------------------------------------


async def test_default_key_prefix_is_cache_colon() -> None:
    """The default key_prefix is 'cache:'."""
    assert CacheManager().key_prefix == "cache:"


async def test_key_prefix_isolation(memory_backend: MemoryBackend) -> None:
    """Two managers with different key_prefix values don't see each other's keys."""
    manager_a = CacheManager(backend=memory_backend, key_prefix="a:")
    manager_b = CacheManager(backend=memory_backend, key_prefix="b:")

    await manager_a.set("key", "from_a")

    assert await manager_a.get("key") == "from_a"
    assert await manager_b.get("key") is None


# --- clear / clear_prefix ------------------------------------------------------


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


async def test_clear_prefix_does_not_count_expired_keys(
    cache_manager: CacheManager,
) -> None:
    """Every backend reports the same count for the same live keys (#178)."""
    await cache_manager.set("kept", 1)
    await cache_manager.set("lapsed", 2, ttl=60)
    backend = cache_manager.backend
    key = "cache:lapsed"
    if isinstance(backend, MemoryBackend):
        backend.cache[key].expiry = time.time() - 1
    else:
        await backend.client.pexpire(backend._make_key(key), 1)  # type: ignore[attr-defined]
        await asyncio.sleep(0.01)

    assert await cache_manager.clear_prefix() == 1


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


async def test_clear_removes_all_manager_keys(memory_backend: MemoryBackend) -> None:
    """clear() wipes all keys under this manager's own namespace only."""
    manager = CacheManager(backend=memory_backend, key_prefix="cache:")
    await manager.set("a", 1)
    await manager.set("b", 2)

    other_entry = CacheEntry(fingerprint="x", content=b'"other"')
    await memory_backend.set("other_namespace:untouched", other_entry, ttl=None)

    removed = await manager.clear()

    assert removed == 2
    assert await manager.get("a") is None
    assert await memory_backend.get("other_namespace:untouched") is not None


async def test_clear_pattern_delegates_to_backend_within_namespace(
    memory_backend: MemoryBackend,
) -> None:
    """clear_pattern() matches glob patterns relative to the manager's key_prefix."""
    manager = CacheManager(backend=memory_backend, key_prefix="cache:")
    await manager.set("user:1", "alice")
    await manager.set("user:2", "bob")
    await manager.set("post:1", "hello")

    removed = await manager.clear_pattern("user:*")

    assert removed == 2
    assert await manager.get("user:1") is None
    assert await manager.get("user:2") is None
    assert await manager.get("post:1") == "hello"


# --- Serialization errors ----------------------------------------------------


async def test_set_non_json_serializable_raises_type_error(
    cache_manager: CacheManager,
) -> None:
    """set() propagates TypeError for a non-JSON-serializable value."""
    with pytest.raises(TypeError):
        await cache_manager.set("key", {1, 2, 3})


async def test_get_with_corrupted_backend_content_returns_default(
    memory_backend: MemoryBackend,
) -> None:
    """get() returns the default when stored content isn't valid JSON."""
    manager = CacheManager(backend=memory_backend)
    cache_key = f"{manager.key_prefix}bad"
    entry = CacheEntry(fingerprint="x", content=b"not valid json")
    await memory_backend.set(cache_key, entry, ttl=60)

    assert await manager.get("bad", default="fallback") == "fallback"


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


# --- Stampede protection (get_or_set) --------------------------------------------


async def test_stampede_protection_default_on(
    memory_backend: MemoryBackend,
) -> None:
    """By default (lock=True since 0.4.0, #280), concurrent misses run factory once."""
    manager = CacheManager(backend=memory_backend)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "data"

    results = await asyncio.gather(
        manager.get_or_set("key", factory),
        manager.get_or_set("key", factory),
    )
    assert manager.lock is True
    assert list(results) == ["data", "data"]
    assert calls == 1


async def test_stampede_protection_manager_lock_false(
    memory_backend: MemoryBackend,
) -> None:
    """With lock=False, concurrent misses for the same key each invoke factory."""
    manager = CacheManager(backend=memory_backend, lock=False)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "data"

    results = await asyncio.gather(
        manager.get_or_set("key", factory),
        manager.get_or_set("key", factory),
    )
    assert list(results) == ["data", "data"]
    assert calls == 2


async def test_stampede_protection_explicit_lock_false_overrides_manager(
    memory_backend: MemoryBackend,
) -> None:
    """An explicit lock=False on get_or_set overrides manager.lock=True."""
    manager = CacheManager(backend=memory_backend, lock=True)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "data"

    results = await asyncio.gather(
        manager.get_or_set("key", factory, lock=False),
        manager.get_or_set("key", factory, lock=False),
    )
    assert list(results) == ["data", "data"]
    assert calls == 2


async def test_stampede_protection_manager_default_lock(
    memory_backend: MemoryBackend,
) -> None:
    """When manager has lock=True, get_or_set without lock parameter uses locking."""
    manager = CacheManager(backend=memory_backend, lock=True)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "computed"

    results = await asyncio.gather(
        manager.get_or_set("key", factory),
        manager.get_or_set("key", factory),
        manager.get_or_set("key", factory),
    )
    assert list(results) == ["computed", "computed", "computed"]
    assert calls == 1


async def test_stampede_protection_single_winner_concurrent_misses(
    memory_backend: MemoryBackend,
) -> None:
    """Concurrent calls with lock=True execute factory once, and all callers get the value."""
    manager = CacheManager(backend=memory_backend)
    calls = 0

    async def slow_factory() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return {"count": 42}

    results = await asyncio.gather(
        *[manager.get_or_set("hot_key", slow_factory, lock=True) for _ in range(8)]
    )
    assert all(r == {"count": 42} for r in results)
    assert calls == 1
    assert await manager.get("hot_key") == {"count": 42}


async def test_stampede_protection_winner_rechecks_cache(
    memory_backend: MemoryBackend,
) -> None:
    """Winner re-checks the cache after acquiring lock and does not run factory if populated."""
    manager = CacheManager(backend=memory_backend)
    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return "from_factory"

    # Pre-populate cache directly before get_or_set
    await manager.set("recheck_key", "pre_existing")

    result = await manager.get_or_set("recheck_key", factory, lock=True)
    assert result == "pre_existing"
    assert calls == 0


async def test_stampede_protection_winner_exception_releases_lock(
    memory_backend: MemoryBackend,
) -> None:
    """If the winner's factory raises, the lock is released in finally, allowing a waiter to take over."""
    manager = CacheManager(backend=memory_backend)
    calls = 0

    async def failing_factory() -> str:
        nonlocal calls
        calls += 1
        msg = "factory failure"
        raise ValueError(msg)

    async def succeeding_factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "recovered"

    task1 = asyncio.create_task(
        manager.get_or_set("fail_key", failing_factory, lock=True)
    )
    # Ensure task1 starts first and acquires lock
    await asyncio.sleep(0.005)
    task2 = asyncio.create_task(
        manager.get_or_set("fail_key", succeeding_factory, lock=True)
    )

    with pytest.raises(ValueError, match="factory failure"):
        await task1

    result2 = await task2
    assert result2 == "recovered"
    assert calls == 2
    assert await manager.get("fail_key") == "recovered"


async def test_stampede_protection_crashed_winner_timeout_fallback(
    memory_backend: MemoryBackend,
    clock: Clock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If winner holds lock indefinitely, waiter times out, logs warning, and computes value."""
    manager = CacheManager(backend=memory_backend)

    # Acquire lock directly, simulating a crashed winner that never released
    lock = CacheLock(
        name=manager._cache_key("stuck_key"),
        ttl=10,
        backend=memory_backend,
    )
    assert await lock.acquire(blocking=False) is True

    calls = 0

    def factory() -> str:
        nonlocal calls
        calls += 1
        return "fallback_value"

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.manager"):
        result = await manager.get_or_set(
            "stuck_key",
            factory,
            lock=True,
            wait_timeout=1.0,
            raise_on_timeout=False,
        )

    assert result == "fallback_value"
    assert calls == 1
    assert any(
        "Cache stampede wait timeout exceeded" in r.getMessage() for r in caplog.records
    )
    await lock.release()


async def test_stampede_protection_crashed_winner_timeout_raises(
    memory_backend: MemoryBackend,
    clock: Clock,
) -> None:
    """If raise_on_timeout=True and wait_timeout elapses, LockTimeoutError is raised."""
    manager = CacheManager(backend=memory_backend)

    # Simulate crashed lock holder
    lock = CacheLock(
        name=manager._cache_key("timeout_key"),
        ttl=10,
        backend=memory_backend,
    )
    assert await lock.acquire(blocking=False) is True

    with pytest.raises(LockTimeoutError, match="timed out") as exc_info:
        await manager.get_or_set(
            "timeout_key",
            lambda: "value",
            lock=True,
            wait_timeout=1.0,
            raise_on_timeout=True,
        )

    assert isinstance(exc_info.value, TimeoutError)
    assert isinstance(exc_info.value, LockTimeoutError)
    await lock.release()


async def test_stampede_protection_crashed_winner_single_takeover(
    memory_backend: MemoryBackend,
) -> None:
    """When a winner crashes with default wait_timeout=None, waiters wait and exactly one takes over."""
    manager = CacheManager(backend=memory_backend, lock=True, lock_ttl=1)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return "recovered"

    # Simulate crashed lock holder
    crashed_lock = CacheLock(
        name=manager._cache_key("crashed_key"), ttl=1, backend=memory_backend
    )
    assert await crashed_lock.acquire(blocking=False) is True

    # 10 concurrent misses: with wait_timeout=None, they do not time out at t=1.
    # When crashed_lock expires at 1s, one waiter acquires the lock, executes factory once,
    # and populates the cache. The other 9 waiters receive the cached value.
    results = await asyncio.wait_for(
        asyncio.gather(
            *(manager.get_or_set("crashed_key", factory) for _ in range(10))
        ),
        timeout=10,
    )
    assert list(results) == ["recovered"] * 10
    assert calls == 1
    assert await manager.get("crashed_key") == "recovered"


async def test_stampede_protection_reentrancy_same_key(
    memory_backend: MemoryBackend,
) -> None:
    """A factory recursively calling get_or_set on the same key skips locking and avoids deadlock."""
    manager = CacheManager(backend=memory_backend, lock=True)
    calls = 0

    async def outer_factory() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        inner = await manager.get_or_set("recursive", lambda: {"inner": 1})
        return {"outer": inner}

    result = await manager.get_or_set("recursive", outer_factory)
    assert result == {"outer": {"inner": 1}}
    assert calls == 1


async def test_stampede_protection_reentrancy_different_keys(
    memory_backend: MemoryBackend,
) -> None:
    """A factory calling get_or_set on a different key acquires a lock for that key normally."""
    manager = CacheManager(backend=memory_backend, lock=True)

    async def outer_factory() -> dict[str, str]:
        inner = await manager.get_or_set("child", lambda: "child_val")
        return {"parent": inner}

    result = await manager.get_or_set("parent", outer_factory)
    assert result == {"parent": "child_val"}
    assert await manager.get("child") == "child_val"
    assert await manager.get("parent") == {"parent": "child_val"}


async def test_stampede_protection_ordering_store_before_release(
    memory_backend: MemoryBackend,
) -> None:
    """The winner stores the value in backend before releasing the lock."""
    import unittest.mock

    manager = CacheManager(backend=memory_backend)
    release_observed_cache: list[bool] = []

    original_release = CacheLock.release

    async def tracking_release(self_lock: CacheLock) -> bool:
        has_val = await memory_backend.get(manager._cache_key("order_key")) is not None
        release_observed_cache.append(has_val)
        return await original_release(self_lock)

    with unittest.mock.patch.object(CacheLock, "release", tracking_release):
        await manager.get_or_set("order_key", lambda: "stored_val", lock=True)

    assert release_observed_cache == [True]


async def test_stampede_protection_non_json_serializable_releases_lock(
    memory_backend: MemoryBackend,
) -> None:
    """Non-JSON-serializable value raises TypeError and releases lock without corrupting cache."""
    manager = CacheManager(backend=memory_backend)

    with pytest.raises(TypeError):
        await manager.get_or_set("bad_val", object, lock=True)

    lock = CacheLock(name=manager._cache_key("bad_val"), backend=memory_backend)
    assert await lock.locked() is False
    assert await manager.get("bad_val") is None

    assert (
        await manager.get_or_set("bad_val", lambda: "recovered", lock=True)
        == "recovered"
    )


async def test_stampede_protection_validation(memory_backend: MemoryBackend) -> None:
    """Validation rejects invalid lock_ttl, wait_timeout, and lock settings."""
    with pytest.raises(TypeError, match="lock must be a bool"):
        CacheManager(backend=memory_backend, lock="no")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="lock must be a bool"):
        CacheManager(backend=memory_backend, lock=1)  # type: ignore[arg-type]

    # None meant "not chosen" in 0.3.x; the manager-wide default is a bool now.
    with pytest.raises(TypeError, match="lock must be a bool, got None"):
        CacheManager(backend=memory_backend, lock=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="ttl must be a positive number"):
        CacheManager(backend=memory_backend, lock_ttl=0)

    with pytest.raises(ValueError, match="ttl must be a positive number"):
        CacheManager(backend=memory_backend, lock_ttl=-10)

    with pytest.raises(ValueError, match="lock_ttl must be a positive int, got None"):
        CacheManager(backend=memory_backend, lock_ttl=None)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="ttl must be an int"):
        CacheManager(backend=memory_backend, lock_ttl=1.5)  # type: ignore[arg-type]

    manager = CacheManager(backend=memory_backend, lock=False)

    with pytest.raises(TypeError, match="lock must be a bool or None"):
        await manager.get_or_set("key", lambda: 1, lock="yes")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="lock must be a bool or None"):
        await manager.get_or_set("key", lambda: 1, lock=1)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="ttl must be a positive number"):
        await manager.get_or_set("key", lambda: 1, lock_ttl=0)

    with pytest.raises(TypeError, match="ttl must be an int"):
        await manager.get_or_set("key", lambda: 1, lock_ttl=1.5)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="wait_timeout must be greater than zero"):
        await manager.get_or_set("key", lambda: 1, wait_timeout=0)

    with pytest.raises(ValueError, match="wait_timeout must be greater than zero"):
        await manager.get_or_set("key", lambda: 1, wait_timeout=-1.0)

    with pytest.raises(TypeError, match="wait_timeout"):
        await manager.get_or_set("key", lambda: 1, wait_timeout=True)

    with pytest.raises(TypeError, match="wait_timeout"):
        await manager.get_or_set("key", lambda: 1, wait_timeout="5")  # type: ignore[arg-type]


async def test_stampede_protection_module_clock_and_sleep_hooks(
    memory_backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Module-level _sleep and _monotonic hooks advance simulated time without real-world delay."""
    virtual_time = 0.0
    sleep_calls: list[float] = []

    def fake_monotonic() -> float:
        return virtual_time

    async def fake_sleep(duration: float) -> None:
        nonlocal virtual_time
        sleep_calls.append(duration)
        virtual_time += duration
        await asyncio.sleep(0)

    from fastapi_cachex import manager as manager_module

    monkeypatch.setattr(manager_module, "_sleep", fake_sleep)
    monkeypatch.setattr(manager_module, "_monotonic", fake_monotonic)

    manager = CacheManager(backend=memory_backend)

    lock = CacheLock(
        name=manager._cache_key("tick_key"), ttl=10, backend=memory_backend
    )
    assert await lock.acquire(blocking=False) is True

    start_real = time.monotonic()
    result = await manager.get_or_set(
        "tick_key",
        lambda: "after_timeout",
        lock=True,
        wait_timeout=1.0,
        raise_on_timeout=False,
    )
    elapsed_real = time.monotonic() - start_real

    assert elapsed_real < 0.2
    assert result == "after_timeout"
    assert len(sleep_calls) >= 1
    assert virtual_time >= 1.0
    await lock.release()


async def test_stampede_protection_release_failure_logged_not_propagated(
    memory_backend: MemoryBackend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed lock release logs a warning and does not overwrite the computed result."""
    import unittest.mock

    manager = CacheManager(backend=memory_backend)

    async def failing_release(_self: CacheLock) -> bool:
        msg = "backend release failure"
        raise RuntimeError(msg)

    with (
        unittest.mock.patch.object(CacheLock, "release", failing_release),
        caplog.at_level(logging.WARNING, logger="fastapi_cachex.manager"),
    ):
        result = await manager.get_or_set("release_fail", lambda: "success", lock=True)

    assert result == "success"
    assert any(
        "Failed to release stampede protection lock" in r.getMessage()
        for r in caplog.records
    )


async def test_stampede_protection_release_failure_preserves_factory_exception(
    memory_backend: MemoryBackend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If factory raises and lock release also fails, the factory exception is preserved."""
    import unittest.mock

    manager = CacheManager(backend=memory_backend)

    async def failing_release(_self: CacheLock) -> bool:
        msg = "backend release failure"
        raise RuntimeError(msg)

    def failing_factory() -> None:
        msg = "factory exception"
        raise ValueError(msg)

    with (
        unittest.mock.patch.object(CacheLock, "release", failing_release),
        caplog.at_level(logging.WARNING, logger="fastapi_cachex.manager"),
        pytest.raises(ValueError, match="factory exception"),
    ):
        await manager.get_or_set("release_fail_factory", failing_factory, lock=True)

    assert any(
        "Failed to release stampede protection lock" in r.getMessage()
        for r in caplog.records
    )


@asynccontextmanager
async def _stampede_backend_context(
    backend_name: str,
) -> AsyncGenerator[BaseCacheBackend, None]:
    backend: BaseCacheBackend
    if backend_name == "memory":
        backend = MemoryBackend()
        backend.start_cleanup()
    elif backend_name == "redis":
        from fastapi_cachex.backends import AsyncRedisCacheBackend

        backend = AsyncRedisCacheBackend(
            host=REDIS_HOST,
            port=REDIS_PORT,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            key_prefix="test_stampede:",
        )
    else:
        from fastapi_cachex.backends import MemcachedBackend

        backend = MemcachedBackend(
            servers=[MEMCACHED_SERVER],
            key_prefix="test_stampede:",
        )

    BackendProxy.set(backend)
    try:
        yield backend
    finally:
        if backend_name == "memcached":
            from fastapi_cachex.backends import MemcachedBackend

            if isinstance(backend, MemcachedBackend):
                await flush_memcached(backend)
        else:
            await backend.clear()
        if backend_name == "memory" and isinstance(backend, MemoryBackend):
            backend.stop_cleanup()
        await backend.aclose()


_STAMPEDE_BACKEND_PARAMS = [
    pytest.param("memory", id="MemoryBackend"),
    pytest.param(
        "redis",
        id="RedisBackend",
        marks=[requires_redis, requires_redis_package],
    ),
    pytest.param(
        "memcached",
        id="MemcachedBackend",
        marks=[requires_memcached],
    ),
]


@pytest.mark.parametrize("backend_name", _STAMPEDE_BACKEND_PARAMS)
async def test_stampede_protection_across_backends(backend_name: str) -> None:
    """Stampede protection works across Memory, Redis, and Memcached backends."""
    async with _stampede_backend_context(backend_name) as backend:
        manager = CacheManager(backend=backend)
        calls = 0

        async def expensive_factory() -> dict[str, str]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return {"status": "ok"}

        results = await asyncio.gather(
            manager.get_or_set("report", expensive_factory),
            manager.get_or_set("report", expensive_factory),
            manager.get_or_set("report", expensive_factory),
        )

        assert list(results) == [{"status": "ok"}, {"status": "ok"}, {"status": "ok"}]
        assert calls == 1
        assert await manager.get("report") == {"status": "ok"}


@pytest.mark.parametrize("backend_name", _STAMPEDE_BACKEND_PARAMS)
async def test_stampede_protection_crashed_winner_across_backends(
    backend_name: str,
) -> None:
    """When a winner crashes, waiters wait for lock expiry and take over across backends."""
    async with _stampede_backend_context(backend_name) as backend:
        manager = CacheManager(backend=backend, lock=True, lock_ttl=1)
        calls = 0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            return "recovered"

        # Simulate crashed winner holding the lock for 1 second
        crashed_lock = CacheLock(
            name=manager._cache_key("crashed_key"), ttl=1, backend=backend
        )
        assert await crashed_lock.acquire(blocking=False) is True

        results = await asyncio.wait_for(
            asyncio.gather(
                *(manager.get_or_set("crashed_key", factory) for _ in range(5))
            ),
            timeout=10,
        )
        assert list(results) == ["recovered"] * 5
        assert calls == 1
        assert await manager.get("crashed_key") == "recovered"


@pytest.mark.parametrize("backend_name", _STAMPEDE_BACKEND_PARAMS)
async def test_stampede_protection_raising_factory_across_backends(
    backend_name: str,
) -> None:
    """If factory raises, lock is released cleanly and subsequent callers can compute across backends."""
    async with _stampede_backend_context(backend_name) as backend:
        manager = CacheManager(backend=backend, lock=True)

        def failing_factory() -> None:
            msg = "database error"
            raise ValueError(msg)

        with pytest.raises(ValueError, match="database error"):
            await manager.get_or_set("fail_key", failing_factory)

        # Lock is released and key is not cached
        lock = CacheLock(name=manager._cache_key("fail_key"), backend=backend)
        assert await lock.locked() is False
        assert await manager.get("fail_key") is None

        # Subsequent caller can compute and store normally
        result = await manager.get_or_set("fail_key", lambda: "recovered")
        assert result == "recovered"
        assert await manager.get("fail_key") == "recovered"


@pytest.mark.parametrize("backend_name", _STAMPEDE_BACKEND_PARAMS)
async def test_stampede_protection_wait_timeout_across_backends(
    backend_name: str,
) -> None:
    """Wait timeout raises LockTimeoutError or falls back to factory across backends."""
    async with _stampede_backend_context(backend_name) as backend:
        manager = CacheManager(backend=backend, lock=True)

        # Hold lock with longer TTL
        lock = CacheLock(
            name=manager._cache_key("timeout_key"), ttl=10, backend=backend
        )
        assert await lock.acquire(blocking=False) is True

        try:
            # 1. raise_on_timeout=True raises LockTimeoutError
            with pytest.raises(LockTimeoutError) as exc_info:
                await manager.get_or_set(
                    "timeout_key",
                    lambda: "value",
                    wait_timeout=0.05,
                    raise_on_timeout=True,
                )
            assert isinstance(exc_info.value, TimeoutError)

            # 2. raise_on_timeout=False falls back to factory
            result = await manager.get_or_set(
                "timeout_key",
                lambda: "fallback_value",
                wait_timeout=0.05,
                raise_on_timeout=False,
            )
            assert result == "fallback_value"
        finally:
            await lock.release()


async def test_stampede_protection_waiter_takes_over_lock(
    memory_backend: MemoryBackend,
) -> None:
    """A waiter acquires the lock on a later tick when the previous lock is released."""
    manager = CacheManager(backend=memory_backend)
    calls = 0

    lock = CacheLock(
        name=manager._cache_key("takeover"), ttl=10, backend=memory_backend
    )
    assert await lock.acquire(blocking=False) is True

    async def release_soon() -> None:
        await asyncio.sleep(0.06)
        await lock.release()

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return "taken_over"

    task = asyncio.create_task(release_soon())
    result = await manager.get_or_set("takeover", factory, lock=True, wait_timeout=1.0)
    await task
    assert result == "taken_over"
    assert calls == 1


async def test_stampede_protection_value_found_on_timeout_check(
    memory_backend: MemoryBackend,
) -> None:
    """If the value appears just as wait_timeout elapses, it returns the cached value."""
    manager = CacheManager(backend=memory_backend)

    lock = CacheLock(
        name=manager._cache_key("late_val"), ttl=10, backend=memory_backend
    )
    assert await lock.acquire(blocking=False) is True

    async def populate_late() -> None:
        await asyncio.sleep(0.06)
        await manager.set("late_val", "late_result")
        await lock.release()

    task = asyncio.create_task(populate_late())
    result = await manager.get_or_set(
        "late_val",
        lambda: "should_not_run",
        lock=True,
        wait_timeout=0.08,
        raise_on_timeout=True,
    )
    await task
    assert result == "late_result"


async def test_stampede_protection_winner_rechecks_inside_execution(
    memory_backend: MemoryBackend,
) -> None:
    """If value appears after lock acquisition check, _execute_as_winner returns cached value."""
    import unittest.mock

    manager = CacheManager(backend=memory_backend)
    first_get = True

    async def sneaky_get(_key: str, default: Any = None) -> Any:
        nonlocal first_get
        if first_get:
            first_get = False
            return default
        return "sneaky_cached"

    with unittest.mock.patch.object(manager, "get", side_effect=sneaky_get):
        result = await manager.get_or_set("sneaky", lambda: "from_factory", lock=True)
        assert result == "sneaky_cached"


# --- Lock default (#280) --------------------------------------------------------


async def test_get_or_set_uses_the_lock_by_default_without_warning(
    memory_backend: MemoryBackend,
) -> None:
    """The 0.3.9 notice is gone: relying on the default is silent."""
    manager = CacheManager(backend=memory_backend)

    # filterwarnings = ["error"] turns a leftover FutureWarning into a failure.

    assert await manager.get_or_set("a", lambda: 1) == 1
    assert manager.lock is True


@pytest.mark.parametrize("lock", [False, True])
async def test_get_or_set_keeps_an_explicit_manager_lock(
    memory_backend: MemoryBackend, lock: bool
) -> None:
    """An explicit manager setting decides for calls that pass no ``lock=``."""
    manager = CacheManager(backend=memory_backend, lock=lock)
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return 1

    results = await asyncio.gather(
        manager.get_or_set("a", factory),
        manager.get_or_set("a", factory),
    )

    assert list(results) == [1, 1]
    assert calls == (1 if lock else 2)
