import asyncio
import socket
import sys
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.lock import CacheLock
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import requires_memcached


def stubbed_backend() -> MemcachedBackend:
    """A fully constructed backend whose client is a stub.

    `HashClient` opens no socket until it is used, so `__init__` runs without
    a server. Going through the real constructor matters: bypassing it with
    `__new__` and setting attributes by hand would leave these tests driving a
    half-built object the moment `__init__` grows a new one.
    """
    backend = MemcachedBackend([MEMCACHED_SERVER])
    backend.client = MagicMock()
    return backend


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
    backend = MemcachedBackend(servers=[MEMCACHED_SERVER])
    await backend.clear()
    return backend


@requires_memcached
def test_memcached_client_waits_for_write_acknowledgements() -> None:
    # With pooling, an unacknowledged write on one socket can still be in flight
    # while a read on another socket is served; every command must be replied to.
    backend = MemcachedBackend(servers=[MEMCACHED_SERVER])
    assert backend.client.use_pooling is True
    assert backend.client.default_kwargs["default_noreply"] is False


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_get(memcached_backend: MemcachedBackend):
    key = "test_key"
    value = CacheEntry(fingerprint="test_etag", content=b"test_content")
    ttl = 60

    await memcached_backend.set(key, value, ttl)
    retrieved_value = await memcached_backend.get(key)

    assert retrieved_value is not None
    assert retrieved_value.fingerprint == value.fingerprint
    assert retrieved_value.content == value.content


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_without_ttl(memcached_backend: MemcachedBackend):
    key = "test_key"
    value = CacheEntry(fingerprint="test_etag", content=b"test_content")

    await memcached_backend.set(key, value)
    retrieved_value = await memcached_backend.get(key)

    assert retrieved_value is not None
    assert retrieved_value.fingerprint == value.fingerprint
    assert retrieved_value.content == value.content


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_delete(memcached_backend: MemcachedBackend):
    key = "test_key"
    value = CacheEntry(fingerprint="test_etag", content=b"test_content")

    await memcached_backend.set(key, value)
    await memcached_backend.delete(key)
    retrieved_value = await memcached_backend.get(key)

    assert retrieved_value is None


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear(memcached_backend: MemcachedBackend):
    key1 = "test_key1"
    value1 = CacheEntry(fingerprint="test_etag1", content=b"test_content1")
    key2 = "test_key2"
    value2 = CacheEntry(fingerprint="test_etag2", content=b"test_content2")

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
    value = CacheEntry(fingerprint="test_etag", content=b"test_value")

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
    value = CacheEntry(fingerprint="test_etag", content=b"test_value")

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
    value = CacheEntry(fingerprint="test_etag", content=b"test_value")

    # Store some test data
    await memcached_backend.set(path, value)

    # Test pattern clearing (should always return 0 as not supported)
    cleared = await memcached_backend.clear_pattern("/users/*")
    assert cleared == 0  # Should return 0 as pattern matching is not supported

    # Verify the original data still exists (as pattern matching is not supported)
    result = await memcached_backend.get(path)
    assert result is not None
    assert result.fingerprint == value.fingerprint


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
async def test_memcached_set_content_bytes(monkeypatch) -> None:
    """Test bytes content round-trip through set/get."""
    backend = MemcachedBackend(servers=[MEMCACHED_SERVER])
    await backend.clear()
    value = CacheEntry(fingerprint="c", content=b"bytes-content")

    await backend.set("bytes-key", value)
    out = await backend.get("bytes-key")
    assert out is not None
    assert out.fingerprint == value.fingerprint
    assert out.content == b"bytes-content"


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


@pytest.mark.asyncio
async def test_memcached_clear_path_raises_when_the_server_is_unreachable() -> None:
    """A connection failure is not "nothing to clear" (#177).

    `clear_path()` used to return 0 for any exception, while `delete()` and
    every other method let it through.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    backend = MemcachedBackend(servers=[f"127.0.0.1:{closed_port}"])

    with pytest.raises(ConnectionRefusedError):
        await backend.clear_path("/nope")


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_clear_path_propagates_client_errors(
    monkeypatch,
    memcached_backend: MemcachedBackend,
) -> None:
    def boom(*args, **kwargs) -> None:
        msg = "delete failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(memcached_backend.client, "delete", boom)
    with pytest.raises(RuntimeError, match="delete failed"):
        await memcached_backend.clear_path("/nope", include_params=False)


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_get_all_keys_empty(
    memcached_backend: MemcachedBackend,
) -> None:
    """Test get_all_keys returns empty list for Memcached (unsupported)."""
    await memcached_backend.clear()
    keys = await memcached_backend.get_all_keys()
    # Memcached doesn't support key enumeration
    assert keys == []


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_get_all_keys_warning(
    memcached_backend: MemcachedBackend,
) -> None:
    """Test get_all_keys issues warning for Memcached."""
    with pytest.warns(
        RuntimeWarning,
        match="Memcached backend does not support key enumeration",
    ):
        keys = await memcached_backend.get_all_keys()
        assert keys == []


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_get_cache_data_empty(
    memcached_backend: MemcachedBackend,
) -> None:
    """Test get_cache_data returns empty dict for Memcached (unsupported)."""
    await memcached_backend.clear()
    cache_data = await memcached_backend.get_cache_data()
    # Memcached doesn't support key enumeration
    assert cache_data == {}


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_get_cache_data_warning(
    memcached_backend: MemcachedBackend,
) -> None:
    """Test get_cache_data issues warning for Memcached."""
    with pytest.warns(
        RuntimeWarning,
        match="Memcached backend does not support key enumeration",
    ):
        cache_data = await memcached_backend.get_cache_data()
        assert cache_data == {}


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_concurrent_calls_do_not_share_a_socket(
    memcached_backend: MemcachedBackend,
) -> None:
    """Every call runs in a worker thread; the client must be thread-safe."""
    entries = {
        f"k{i}": CacheEntry(fingerprint=f"e{i}", content=f"v{i}".encode())
        for i in range(40)
    }

    await asyncio.gather(*(memcached_backend.set(k, v, 60) for k, v in entries.items()))
    results = await asyncio.gather(*(memcached_backend.get(k) for k in entries))

    assert results == list(entries.values())


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_increment_creates_then_adds(
    memcached_backend: MemcachedBackend,
) -> None:
    assert await memcached_backend.increment("hits") == 1
    assert await memcached_backend.increment("hits") == 2
    assert await memcached_backend.increment("hits", 5) == 7
    assert await memcached_backend.increment("hits", -3) == 4

    assert await memcached_backend.get("hits") == counter_entry(4)


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_increment_decrement_stops_at_zero(
    memcached_backend: MemcachedBackend,
) -> None:
    await memcached_backend.increment("floor", 2)

    assert await memcached_backend.increment("floor", -5) == 0
    assert await memcached_backend.increment("missing", -5) == 0


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_increment_honors_ttl(
    memcached_backend: MemcachedBackend,
) -> None:
    await memcached_backend.increment("window", ttl=1)
    await memcached_backend.increment("window", ttl=3600)
    await asyncio.sleep(2)

    assert await memcached_backend.get("window") is None
    assert await memcached_backend.increment("window", ttl=1) == 1


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_increment_rejects_a_cached_response(
    memcached_backend: MemcachedBackend,
) -> None:
    await memcached_backend.set("page", CacheEntry(fingerprint="e", content=b"<html>"))

    with pytest.raises(CacheXError, match="not a counter"):
        await memcached_backend.increment("page")


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_increment_is_atomic_under_concurrency(
    memcached_backend: MemcachedBackend,
) -> None:
    results = await asyncio.gather(
        *(memcached_backend.increment("race", ttl=60) for _ in range(30))
    )

    assert sorted(results) == list(range(1, 31))


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_get_and_delete_returns_then_removes(
    memcached_backend: MemcachedBackend,
) -> None:
    value = CacheEntry(fingerprint="e", content=b"once", media_type="text/plain")
    await memcached_backend.set("once", value, 60)

    assert await memcached_backend.get_and_delete("once") == value
    assert await memcached_backend.get_and_delete("once") is None
    assert await memcached_backend.get("once") is None


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_get_and_delete_loses_the_race_when_delete_fails(
    memcached_backend: MemcachedBackend, monkeypatch
) -> None:
    await memcached_backend.set("once", CacheEntry(fingerprint="e", content=b"x"), 60)
    monkeypatch.setattr(memcached_backend.client, "delete", lambda *a, **kw: False)

    assert await memcached_backend.get_and_delete("once") is None


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_get_and_delete_has_exactly_one_winner(
    memcached_backend: MemcachedBackend,
) -> None:
    value = CacheEntry(fingerprint="e", content=b"once")
    await memcached_backend.set("once", value, 60)

    results = await asyncio.gather(
        *(memcached_backend.get_and_delete("once") for _ in range(20))
    )

    assert results.count(value) == 1
    assert results.count(None) == 19


def test_legal_keys_are_left_alone() -> None:
    """Entries written by earlier versions must stay readable."""
    backend = stubbed_backend()

    assert backend._make_key("GET|||localhost|||/users/1|||") == (
        "fastapi_cachex:GET|||localhost|||/users/1|||"
    )


@pytest.mark.parametrize(
    "key",
    [
        "GET|||localhost|||/foo bar|||",  # ASGI percent-decodes the path
        "GET|||localhost|||/café|||",  # non-ASCII path
        "GET|||localhost|||/x|||\n",  # control character
        "GET|||localhost|||/search|||q=" + "a" * 400,  # over 250 bytes
    ],
)
def test_keys_memcached_would_refuse_are_hashed(key: str) -> None:
    """A key Memcached rejects becomes a digest instead of an exception."""
    backend = stubbed_backend()

    made = backend._make_key(key)

    assert made != f"fastapi_cachex:{key}"
    assert made.startswith("fastapi_cachex:")
    encoded = made.encode("utf-8")
    assert len(encoded) <= 250
    assert all(0x21 <= byte < 0x7F for byte in encoded)


@requires_memcached
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "GET|||localhost|||/foo bar|||",
        "GET|||localhost|||/café|||",
        "GET|||localhost|||/search|||q=" + "a" * 1024,
    ],
)
async def test_illegal_keys_round_trip_through_the_server(
    memcached_backend: MemcachedBackend, key: str
) -> None:
    """These used to raise MemcacheIllegalInputError straight out of `@cache`."""
    value = CacheEntry(fingerprint="e", content=b"content")

    await memcached_backend.set(key, value, 60)
    retrieved = await memcached_backend.get(key)

    assert retrieved is not None
    assert retrieved.content == b"content"

    await memcached_backend.delete(key)
    assert await memcached_backend.get(key) is None


@pytest.mark.asyncio
async def test_ttl_beyond_thirty_days_is_sent_as_an_absolute_timestamp() -> None:
    """Memcached reads an exptime over 30 days as a Unix timestamp, not a duration."""
    import time

    backend = stubbed_backend()

    sixty_days = 60 * 24 * 60 * 60
    await backend.set("k", CacheEntry(fingerprint="e", content=b"v"), sixty_days)

    expire = backend.client.set.call_args[0][2]
    now = int(time.time())
    assert now < expire <= now + sixty_days + 5


@pytest.mark.asyncio
@pytest.mark.parametrize(("ttl", "expected"), [(None, 0), (60, 60)])
async def test_short_ttls_stay_relative(ttl: int | None, expected: int) -> None:
    """Durations inside the boundary are passed straight through."""
    backend = stubbed_backend()

    await backend.set("k", CacheEntry(fingerprint="e", content=b"v"), ttl)

    assert backend.client.set.call_args[0][2] == expected


@requires_memcached
def test_cached_route_with_a_space_in_the_path_is_served() -> None:
    """End to end: the decoded path builds a key Memcached would have refused."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fastapi_cachex.cache import cache
    from fastapi_cachex.proxy import BackendProxy

    backend = MemcachedBackend(servers=[MEMCACHED_SERVER])
    previous = BackendProxy.get()
    BackendProxy.set(backend)
    try:
        app = FastAPI()

        @app.get("/foo bar")
        @cache(ttl=60)
        async def spaced() -> dict[str, str]:
            return {"ok": "yes"}

        client = TestClient(app)

        first = client.get("/foo%20bar")
        second = client.get("/foo%20bar")

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json() == {"ok": "yes"}
    finally:
        BackendProxy.set(previous)


@pytest.mark.asyncio
async def test_increment_reports_a_counter_that_vanished_mid_call() -> None:
    """ADD then INCR is two round-trips; the entry can expire in between.

    Memcached has no way to make the pair atomic, so `increment` has to
    surface the loss instead of returning `None` as if it were a count.
    """
    backend = stubbed_backend()
    # INCR keeps missing: the key is gone again by the time ADD's retry runs.
    backend.client.incr.return_value = None

    with pytest.raises(CacheXError, match="Counter vanished between ADD and INCR"):
        await backend.increment("k")

    assert backend.client.add.call_count == 1


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_if_absent_stores_only_the_first_value(
    memcached_backend: MemcachedBackend,
):
    first = CacheEntry(fingerprint="lock", content=b"owner-a")
    second = CacheEntry(fingerprint="lock", content=b"owner-b")

    assert await memcached_backend.set_if_absent("slot", first, 60) is True
    assert await memcached_backend.set_if_absent("slot", second, 60) is False
    assert await memcached_backend.get("slot") == first


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_if_absent_applies_the_ttl(
    memcached_backend: MemcachedBackend,
):
    entry = CacheEntry(fingerprint="lock", content=b"owner-a")

    assert await memcached_backend.set_if_absent("slot", entry, 1) is True
    await asyncio.sleep(2.1)
    assert await memcached_backend.set_if_absent("slot", entry, 60) is True


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_set_if_absent_has_exactly_one_winner(
    memcached_backend: MemcachedBackend,
):
    results = await asyncio.gather(
        *(
            memcached_backend.set_if_absent(
                "slot", CacheEntry(fingerprint="lock", content=str(i).encode()), 60
            )
            for i in range(20)
        )
    )

    assert results.count(True) == 1
    winner = results.index(True)
    assert await memcached_backend.get("slot") == CacheEntry(
        fingerprint="lock", content=str(winner).encode()
    )


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_delete_if_equals_removes_only_a_matching_entry(
    memcached_backend: MemcachedBackend,
):
    mine = CacheEntry(fingerprint="lock", content=b"owner-a")
    theirs = CacheEntry(fingerprint="lock", content=b"owner-b")
    await memcached_backend.set("slot", theirs, 60)

    assert await memcached_backend.delete_if_equals("slot", mine) is False
    assert await memcached_backend.get("slot") == theirs

    assert await memcached_backend.delete_if_equals("slot", theirs) is True
    assert await memcached_backend.get("slot") is None
    assert await memcached_backend.delete_if_equals("slot", theirs) is False
    # The CAS-expired key is really gone: it can be claimed again at once.
    assert await memcached_backend.set_if_absent("slot", mine, 60) is True


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_delete_if_equals_matches_a_counter(
    memcached_backend: MemcachedBackend,
):
    await memcached_backend.increment("hits", 3)

    assert await memcached_backend.delete_if_equals("hits", counter_entry(2)) is False
    assert await memcached_backend.delete_if_equals("hits", counter_entry(3)) is True
    assert await memcached_backend.get("hits") is None


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_delete_if_equals_keeps_a_value_written_after_the_compare(
    memcached_backend: MemcachedBackend,
):
    """Between GETS and the CAS, another holder claims the key; the CAS token
    no longer matches, so the new holder's entry survives."""
    mine = CacheEntry(fingerprint="lock", content=b"owner-a")
    await memcached_backend.set("slot", mine, 60)

    client = memcached_backend.client
    original_gets = client.gets

    def gets_then_overwrite(key):
        result = original_gets(key)
        client.set(key, b'{"overwritten": true}', 60)
        return result

    client.gets = gets_then_overwrite
    try:
        assert await memcached_backend.delete_if_equals("slot", mine) is False
    finally:
        client.gets = original_gets

    raw = await asyncio.to_thread(client.get, memcached_backend._make_key("slot"))
    assert raw == b'{"overwritten": true}'


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_expire_if_equals_updates_ttl_only_when_matching(
    memcached_backend: MemcachedBackend,
):
    mine = CacheEntry(fingerprint="lock", content=b"owner-a")
    theirs = CacheEntry(fingerprint="lock", content=b"owner-b")
    await memcached_backend.set("slot", theirs, 30)

    assert await memcached_backend.expire_if_equals("slot", mine, 60) is False
    assert await memcached_backend.expire_if_equals("slot", theirs, 60) is True
    assert await memcached_backend.get("slot") == theirs
    assert await memcached_backend.expire_if_equals("missing", theirs, 60) is False


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_expire_if_equals_keeps_a_value_written_after_the_compare(
    memcached_backend: MemcachedBackend,
):
    mine = CacheEntry(fingerprint="lock", content=b"owner-a")
    await memcached_backend.set("slot", mine, 60)

    client = memcached_backend.client
    original_gets = client.gets

    def gets_then_overwrite(key):
        result = original_gets(key)
        client.set(key, b'{"overwritten": true}', 60)
        return result

    client.gets = gets_then_overwrite
    try:
        assert await memcached_backend.expire_if_equals("slot", mine, 120) is False
    finally:
        client.gets = original_gets

    raw = await asyncio.to_thread(client.get, memcached_backend._make_key("slot"))
    assert raw == b'{"overwritten": true}'


@requires_memcached
@pytest.mark.asyncio
async def test_lock_lifecycle_with_memcached(
    memcached_backend: MemcachedBackend,
) -> None:
    lock1 = CacheLock("memcached_job", ttl=30, backend=memcached_backend)
    lock2 = CacheLock("memcached_job", ttl=30, backend=memcached_backend)

    assert await lock1.acquire(blocking=False) is True
    assert await lock2.acquire(blocking=False) is False
    assert await lock1.extend(60) is True
    assert await lock1.release() is True
