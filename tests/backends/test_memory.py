import asyncio
import time

import pytest
import pytest_asyncio

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import COUNTER_FINGERPRINT
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry


@pytest_asyncio.fixture
def memory_backend():
    return MemoryBackend()


@pytest.mark.parametrize("interval", [0, -5])
def test_cleanup_interval_must_be_positive(interval: int) -> None:
    """A non-positive interval made the cleanup loop spin (#180)."""
    with pytest.raises(ValueError, match="cleanup_interval must be positive"):
        MemoryBackend(cleanup_interval=interval)


@pytest.mark.asyncio
async def test_memory_backend_set_get(memory_backend: MemoryBackend):
    key = "test_key"
    value = CacheEntry(
        fingerprint="test_etag",
        content=b"test_value",
        media_type="application/json",
    )
    ttl = 60

    await memory_backend.set(key, value, ttl)
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value == value


@pytest.mark.asyncio
async def test_memory_backend_get_nonexistent_key(memory_backend: MemoryBackend):
    key = "nonexistent_key"
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value is None


@pytest.mark.asyncio
async def test_memory_backend_delete(memory_backend: MemoryBackend):
    key = "test_key"
    value = CacheEntry(
        fingerprint="test_etag",
        content=b"test_value",
        media_type="application/json",
    )
    ttl = 60

    await memory_backend.set(key, value, ttl)
    await memory_backend.delete(key)
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value is None


@pytest.mark.asyncio
async def test_memory_backend_clear(memory_backend: MemoryBackend):
    key1 = "test_key1"
    value1 = CacheEntry(
        fingerprint="test_etag1",
        content=b"test_value1",
        media_type="application/json",
    )
    key2 = "test_key2"
    value2 = CacheEntry(
        fingerprint="test_etag2",
        content=b"test_value2",
        media_type="application/json",
    )
    ttl = 60

    await memory_backend.set(key1, value1, ttl)
    await memory_backend.set(key2, value2, ttl)
    await memory_backend.clear()

    retrieved_value1 = await memory_backend.get(key1)
    retrieved_value2 = await memory_backend.get(key2)

    assert retrieved_value1 is None
    assert retrieved_value2 is None


@pytest.mark.asyncio
async def test_memory_backend_ttl_expiry(memory_backend: MemoryBackend):
    key = "test_key"
    value = CacheEntry(
        fingerprint="test_etag",
        content=b"test_value",
        media_type="application/json",
    )
    ttl = 1

    await memory_backend.set(key, value, ttl)
    await asyncio.sleep(2)  # Wait for the TTL to expire
    retrieved_value = await memory_backend.get(key)

    assert retrieved_value is None


@pytest.mark.asyncio
async def test_memory_backend_cleanup(memory_backend: MemoryBackend):
    key1 = "test_key1"
    value1 = CacheEntry(
        fingerprint="test_etag1",
        content=b"test_value1",
        media_type="application/json",
    )
    ttl1 = 1
    key2 = "test_key2"
    value2 = CacheEntry(
        fingerprint="test_etag2",
        content=b"test_value2",
        media_type="application/json",
    )
    ttl2 = 60

    await memory_backend.set(key1, value1, ttl1)
    await memory_backend.set(key2, value2, ttl2)
    await asyncio.sleep(2)  # Wait for the TTL of key1 to expire
    await memory_backend.cleanup()

    retrieved_value1 = await memory_backend.get(key1)
    retrieved_value2 = await memory_backend.get(key2)

    assert retrieved_value1 is None
    assert retrieved_value2 == value2


@pytest.mark.asyncio
async def test_memory_backend_start_cleanup(memory_backend: MemoryBackend):
    memory_backend.start_cleanup()
    assert memory_backend._cleanup_task is not None
    assert not memory_backend._cleanup_task.done()
    memory_backend.stop_cleanup()  # Clean up after test


@pytest.mark.asyncio
async def test_memory_backend_stop_cleanup(memory_backend: MemoryBackend):
    memory_backend.start_cleanup()
    assert memory_backend._cleanup_task is not None
    memory_backend.stop_cleanup()
    assert memory_backend._cleanup_task is None


@pytest.mark.asyncio
async def test_memory_backend_double_start_cleanup(memory_backend: MemoryBackend):
    memory_backend.start_cleanup()
    original_task = memory_backend._cleanup_task
    memory_backend.start_cleanup()  # Start again
    assert memory_backend._cleanup_task is original_task  # Should be the same task
    memory_backend.stop_cleanup()  # Clean up after test


@pytest.mark.asyncio
async def test_memory_backend_stop_cleanup_when_not_running(
    memory_backend: MemoryBackend,
):
    memory_backend.stop_cleanup()  # Should not raise any error
    assert memory_backend._cleanup_task is None


@pytest.mark.asyncio
async def test_memory_backend_cleanup_task_impl():
    """The sweeper itself has to drop expired entries.

    Reading the keys back through `get` proves nothing about the sweeper:
    `get` deletes an expired entry on its way to returning `None`, so that
    assertion holds even if the background task never removes anything. The
    dictionary is inspected directly instead, and neither key is ever read.
    """
    backend = MemoryBackend(cleanup_interval=1)
    expiring = CacheEntry(fingerprint="test_etag1", content=b"test_value1")
    surviving = CacheEntry(fingerprint="test_etag2", content=b"test_value2")

    await backend.set("expiring", expiring, ttl=1)
    await backend.set("surviving", surviving, ttl=60)
    backend.start_cleanup()

    try:
        # One full interval plus the entry's own TTL.
        await asyncio.sleep(2)

        assert "expiring" not in backend.cache
        assert backend.cache["surviving"].value == surviving
    finally:
        backend.stop_cleanup()


@pytest.mark.asyncio
async def test_memory_backend_clear_path(memory_backend: MemoryBackend):
    # Set up test data with proper cache key format: method|||host|||path|||query_params
    # default_key_builder always appends a trailing separator for query_params
    path = "/test"
    value1 = CacheEntry(fingerprint="test_etag1", content=b"test_value1")
    value2 = CacheEntry(fingerprint="test_etag2", content=b"test_value2")
    value3 = CacheEntry(fingerprint="test_etag3", content=b"test_value3")

    # Store data with method|||host|||path||| format (trailing separator, empty params)
    await memory_backend.set(f"GET|||localhost|||{path}|||", value1)
    await memory_backend.set(f"POST|||localhost|||{path}|||", value2)
    await memory_backend.set("GET|||localhost|||/other|||", value3)

    # Test clearing without parameters - should clear entries with exact path
    cleared = await memory_backend.clear_path(path, include_params=False)
    assert cleared == 2  # Should clear GET and POST entries with /test path

    # Verify the other path's data still exists
    other_value = await memory_backend.get("GET|||localhost|||/other|||")
    assert other_value == value3


@pytest.mark.asyncio
async def test_memory_backend_clear_pattern(memory_backend: MemoryBackend):
    # Set up test data with proper cache key format: method|||host|||path|||query_params
    value1 = CacheEntry(fingerprint="test_etag1", content=b"test_value1")
    value2 = CacheEntry(fingerprint="test_etag2", content=b"test_value2")
    value3 = CacheEntry(fingerprint="test_etag3", content=b"test_value3")

    # Store data with method|||host|||path||| format (trailing separator)
    await memory_backend.set("GET|||localhost|||/users/123|||", value1)
    await memory_backend.set("POST|||localhost|||/users/456|||", value2)
    await memory_backend.set("GET|||localhost|||/posts/789|||", value3)

    # The pattern matches whole keys, so the method and host must be written out
    cleared = await memory_backend.clear_pattern("*|||localhost|||/users/*")
    assert cleared == 2  # Should clear both user entries

    # Verify the posts data still exists
    posts_value = await memory_backend.get("GET|||localhost|||/posts/789|||")
    assert posts_value == value3


@pytest.mark.asyncio
async def test_memory_backend_clear_pattern_needs_a_whole_key_glob(
    memory_backend: MemoryBackend,
):
    """A path-only pattern matches nothing, exactly as it does on Redis.

    This used to clear the entry, because the pattern was matched against the
    path component alone. `clear_path` is the method for clearing by path.
    """
    value = CacheEntry(fingerprint="e1", content=b"v1")
    await memory_backend.set("GET|||localhost|||/users/123|||", value)

    with pytest.warns(RuntimeWarning, match="clear_path"):
        assert await memory_backend.clear_pattern("/users/*") == 0
    assert await memory_backend.get("GET|||localhost|||/users/123|||") == value

    assert await memory_backend.clear_path("/users/123") == 1


@pytest.mark.asyncio
async def test_memory_backend_clear_pattern_separator_less_keys(
    memory_backend: MemoryBackend,
):
    """clear_pattern must also match keys with no method|||host|||path format,
    e.g. CacheManager ("cache:...") or StateManager ("oauth_state:...") keys.
    """
    value1 = CacheEntry(fingerprint="e1", content=b"v1")
    value2 = CacheEntry(fingerprint="e2", content=b"v2")
    value3 = CacheEntry(fingerprint="e3", content=b"v3")

    await memory_backend.set("cache:user:123", value1)
    await memory_backend.set("cache:user:456", value2)
    await memory_backend.set("cache:post:789", value3)

    cleared = await memory_backend.clear_pattern("cache:user:*")
    assert cleared == 2

    assert await memory_backend.get("cache:user:123") is None
    assert await memory_backend.get("cache:user:456") is None
    assert await memory_backend.get("cache:post:789") == value3


@pytest.mark.asyncio
async def test_memory_backend_clear_path_with_colon_in_path(
    memory_backend: MemoryBackend,
) -> None:
    """Paths containing colons (e.g. gitlab:template) must be clearable."""
    value = CacheEntry(fingerprint="e", content=b"v")

    await memory_backend.set("GET|||localhost:8000|||/gitlab:template|||", value)
    await memory_backend.set(
        "GET|||localhost:8000|||/gitlab:template:projects|||", value
    )
    await memory_backend.set("GET|||localhost:8000|||/gitlab:template|||tag=v1", value)

    # include_params=False: only empty-query-param entries
    cleared = await memory_backend.clear_path("/gitlab:template", include_params=False)
    assert cleared == 1
    assert (
        await memory_backend.get("GET|||localhost:8000|||/gitlab:template|||") is None
    )
    # Sub-path and param variant remain
    assert (
        await memory_backend.get("GET|||localhost:8000|||/gitlab:template:projects|||")
        == value
    )
    assert (
        await memory_backend.get("GET|||localhost:8000|||/gitlab:template|||tag=v1")
        == value
    )

    # include_params=True: clear remaining entries with that exact path
    cleared = await memory_backend.clear_path("/gitlab:template", include_params=True)
    assert cleared == 1  # only the param variant was left
    assert (
        await memory_backend.get("GET|||localhost:8000|||/gitlab:template|||tag=v1")
        is None
    )
    # Sub-path is a different path, should remain
    assert (
        await memory_backend.get("GET|||localhost:8000|||/gitlab:template:projects|||")
        == value
    )


@pytest.mark.asyncio
async def test_memory_backend_clear_path_include_params(
    memory_backend: MemoryBackend,
) -> None:
    """include_params=True should clear path entries with and without query params."""
    value = CacheEntry(fingerprint="e", content=b"v")

    await memory_backend.set("GET|||localhost|||/items|||", value)
    await memory_backend.set("GET|||localhost|||/items|||page=2", value)
    await memory_backend.set("GET|||localhost|||/other|||", value)

    cleared = await memory_backend.clear_path("/items", include_params=True)
    assert cleared == 2
    assert await memory_backend.get("GET|||localhost|||/items|||") is None
    assert await memory_backend.get("GET|||localhost|||/items|||page=2") is None
    assert await memory_backend.get("GET|||localhost|||/other|||") == value


@pytest.mark.asyncio
async def test_memory_backend_clear_path_direct_key(
    memory_backend: MemoryBackend,
) -> None:
    """clear_path should delete direct keys stored without ||| separators."""
    value = CacheEntry(fingerprint="e", content=b"v")

    await memory_backend.set("gitlab:template", value)
    await memory_backend.set("gitlab:template:projects", value)
    await memory_backend.set("gitlab:template:by_tag", value)

    cleared = await memory_backend.clear_path("gitlab:template", include_params=False)
    assert cleared == 1
    assert await memory_backend.get("gitlab:template") is None
    assert await memory_backend.get("gitlab:template:projects") == value
    assert await memory_backend.get("gitlab:template:by_tag") == value


@pytest.mark.asyncio
async def test_memory_backend_clear_path_direct_key_and_separator_key(
    memory_backend: MemoryBackend,
) -> None:
    """clear_path should delete both direct keys and separator-format keys."""
    value = CacheEntry(fingerprint="e", content=b"v")

    await memory_backend.set("my:path", value)
    await memory_backend.set("GET|||localhost|||my:path|||", value)

    cleared = await memory_backend.clear_path("my:path", include_params=False)
    assert cleared == 2
    assert await memory_backend.get("my:path") is None
    assert await memory_backend.get("GET|||localhost|||my:path|||") is None


@pytest.mark.asyncio
async def test_memory_backend_get_all_keys_empty(memory_backend: MemoryBackend):
    """Test get_all_keys returns empty list for empty cache."""
    keys = await memory_backend.get_all_keys()
    assert keys == []


@pytest.mark.asyncio
async def test_memory_backend_get_all_keys_with_entries(
    memory_backend: MemoryBackend,
) -> None:
    """Test get_all_keys returns all cache keys."""
    key1 = "GET|||localhost|||/users"
    key2 = "POST|||localhost|||/users"
    key3 = "GET|||localhost|||/posts"

    value = CacheEntry(fingerprint="test_etag", content=b"test_value")

    await memory_backend.set(key1, value)
    await memory_backend.set(key2, value)
    await memory_backend.set(key3, value)

    keys = await memory_backend.get_all_keys()
    assert sorted(keys) == sorted([key1, key2, key3])
    assert len(keys) == 3


@pytest.mark.asyncio
async def test_memory_backend_get_cache_data_empty(memory_backend: MemoryBackend):
    """Test get_cache_data returns empty dict for empty cache."""
    cache_data = await memory_backend.get_cache_data()
    assert cache_data == {}


@pytest.mark.asyncio
async def test_memory_backend_get_cache_data_with_entries(
    memory_backend: MemoryBackend,
) -> None:
    """Test get_cache_data returns all cache data with expiry."""
    key1 = "GET|||localhost|||/users"
    key2 = "POST|||localhost|||/users"
    value1 = CacheEntry(fingerprint="etag1", content=b"value1")
    value2 = CacheEntry(fingerprint="etag2", content=b"value2")

    # Set with TTL
    await memory_backend.set(key1, value1, ttl=3600)
    # Set without TTL
    await memory_backend.set(key2, value2)

    cache_data = await memory_backend.get_cache_data()

    assert len(cache_data) == 2
    assert key1 in cache_data
    assert key2 in cache_data

    # Verify values
    stored_value1, expiry1 = cache_data[key1]
    stored_value2, expiry2 = cache_data[key2]

    assert stored_value1 == value1
    assert stored_value2 == value2

    # Verify expiry (key1 should have expiry, key2 should not)
    assert expiry1 is not None
    assert expiry2 is None


@pytest.mark.asyncio
async def test_memory_backend_get_cache_data_expired_entries(
    memory_backend: MemoryBackend,
) -> None:
    """Test get_cache_data includes expired entries."""
    key = "GET|||localhost|||/test"
    value = CacheEntry(fingerprint="test_etag", content=b"test_value")

    # Set with very short TTL
    await memory_backend.set(key, value, ttl=1)

    # Wait for expiry
    await asyncio.sleep(1.1)

    cache_data = await memory_backend.get_cache_data()

    # Expired entries should still be in the raw cache data
    # but get() won't return them
    assert key in cache_data
    retrieved_value, expiry = cache_data[key]
    assert retrieved_value == value
    assert expiry is not None
    assert expiry <= time.time()


def test_ensure_cleanup_started_without_event_loop() -> None:
    """_ensure_cleanup_started must not raise and must not leak unawaited coroutines
    when called outside any running event loop (e.g. at import time or in __init__).

    Run in a fresh thread so there is guaranteed to be no running event loop,
    regardless of whether the test runner itself has one active.
    """
    import concurrent.futures
    import warnings

    def run_in_thread() -> bool:
        backend = MemoryBackend()
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            backend._ensure_cleanup_started()
        return backend._cleanup_task is None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        no_task_created = executor.submit(run_in_thread).result()

    assert no_task_created


@pytest.mark.asyncio
async def test_memory_backend_increment_creates_then_adds(
    memory_backend: MemoryBackend,
):
    assert await memory_backend.increment("hits") == 1
    assert await memory_backend.increment("hits") == 2
    assert await memory_backend.increment("hits", 5) == 7
    assert await memory_backend.increment("hits", -3) == 4

    entry = await memory_backend.get("hits")
    assert entry == counter_entry(4)
    assert entry is not None
    assert entry.fingerprint == COUNTER_FINGERPRINT


@pytest.mark.asyncio
async def test_memory_backend_increment_applies_ttl_only_on_creation(
    memory_backend: MemoryBackend,
):
    await memory_backend.increment("window", ttl=60)
    first_expiry = memory_backend.cache["window"].expiry
    assert first_expiry is not None

    await memory_backend.increment("window", ttl=3600)

    assert memory_backend.cache["window"].expiry == first_expiry
    assert memory_backend.cache["window"].value == counter_entry(2)


@pytest.mark.asyncio
async def test_memory_backend_increment_without_ttl_never_expires(
    memory_backend: MemoryBackend,
):
    await memory_backend.increment("forever")

    assert memory_backend.cache["forever"].expiry is None


@pytest.mark.asyncio
async def test_memory_backend_increment_restarts_an_expired_counter(
    memory_backend: MemoryBackend,
):
    await memory_backend.increment("stale", 9, ttl=60)
    memory_backend.cache["stale"].expiry = time.time() - 1

    assert await memory_backend.increment("stale", ttl=60) == 1


@pytest.mark.asyncio
async def test_memory_backend_increment_rejects_a_cached_response(
    memory_backend: MemoryBackend,
):
    await memory_backend.set("page", CacheEntry(fingerprint="e", content=b"<html>"))

    with pytest.raises(CacheXError, match="not a counter"):
        await memory_backend.increment("page")


@pytest.mark.asyncio
async def test_memory_backend_increment_is_atomic_under_concurrency(
    memory_backend: MemoryBackend,
):
    results = await asyncio.gather(
        *(memory_backend.increment("race", ttl=60) for _ in range(100))
    )

    assert sorted(results) == list(range(1, 101))
    assert await memory_backend.get("race") == counter_entry(100)


@pytest.mark.asyncio
async def test_memory_backend_get_and_delete_returns_then_removes(
    memory_backend: MemoryBackend,
):
    value = CacheEntry(fingerprint="e", content=b"once")
    await memory_backend.set("once", value, 60)

    assert await memory_backend.get_and_delete("once") == value
    assert await memory_backend.get_and_delete("once") is None
    assert await memory_backend.get("once") is None


@pytest.mark.asyncio
async def test_memory_backend_get_and_delete_drops_an_expired_entry(
    memory_backend: MemoryBackend,
):
    await memory_backend.set("stale", CacheEntry(fingerprint="e", content=b"x"), 60)
    memory_backend.cache["stale"].expiry = time.time() - 1

    assert await memory_backend.get_and_delete("stale") is None
    assert "stale" not in memory_backend.cache


@pytest.mark.asyncio
async def test_memory_backend_get_and_delete_has_exactly_one_winner(
    memory_backend: MemoryBackend,
):
    value = CacheEntry(fingerprint="e", content=b"once")
    await memory_backend.set("once", value, 60)

    results = await asyncio.gather(
        *(memory_backend.get_and_delete("once") for _ in range(20))
    )

    assert results.count(value) == 1
    assert results.count(None) == 19


@pytest.mark.asyncio
async def test_memory_delete_many_counts_only_existing_keys(
    memory_backend: MemoryBackend,
):
    await memory_backend.set("a", CacheEntry(fingerprint="e", content=b"1"))
    await memory_backend.set("b", CacheEntry(fingerprint="e", content=b"2"))
    await memory_backend.set("keep", CacheEntry(fingerprint="e", content=b"3"))

    assert await memory_backend.delete_many(["a", "b", "missing"]) == 2
    assert await memory_backend.get("a") is None
    assert await memory_backend.get("keep") is not None


@pytest.mark.asyncio
async def test_write_only_use_starts_the_cleanup_task():
    """A backend that is only written to still needs its sweeper running.

    Only `get` used to start it, so a write-mostly caller — `StateManager`
    creates states without ever reading them back through `get` — accumulated
    expired entries with nothing to remove them.
    """
    backend = MemoryBackend(cleanup_interval=1)

    await backend.set("gone", CacheEntry(fingerprint="e", content=b"v"), ttl=1)
    await backend.set("stays", CacheEntry(fingerprint="e", content=b"v"), ttl=60)

    try:
        await asyncio.sleep(2)

        # Nothing here ever calls `get`, so only the sweeper can have removed
        # the expired key — a task that merely exists would leave it in place.
        assert "gone" not in backend.cache
        assert "stays" in backend.cache
    finally:
        backend.stop_cleanup()


@pytest.mark.asyncio
async def test_get_evicts_the_expired_entry_it_skips():
    """A miss on an expired key must also free the memory it was holding.

    Asserting that `get` returns `None` says nothing about this: it returns
    `None` whether or not the entry is dropped. The dictionary is checked
    directly, because between sweeps this is the only thing that reclaims an
    expired entry the caller happened to ask for.
    """
    backend = MemoryBackend()
    await backend.set("k", CacheEntry(fingerprint="e", content=b"v"), ttl=1)

    try:
        await asyncio.sleep(1.05)

        assert await backend.get("k") is None
        assert "k" not in backend.cache
    finally:
        backend.stop_cleanup()


@pytest.mark.asyncio
async def test_read_only_use_starts_the_cleanup_task():
    """A read-mostly caller needs the sweeper too.

    `set` starts it, but a process that only reads a cache another process
    fills would never sweep its own copy of nothing -- and, more to the point,
    a backend that is read before it is written must not be left without one.
    """
    backend = MemoryBackend()

    before = backend._cleanup_task

    try:
        await backend.get("never-stored")
        after = backend._cleanup_task

        assert before is None
        assert after is not None
        assert not after.done()
    finally:
        backend.stop_cleanup()


@pytest.mark.asyncio
async def test_cleanup_leaves_live_entries_alone():
    """A sweep with nothing to do must not touch what is still valid."""
    backend = MemoryBackend()
    await backend.set("a", CacheEntry(fingerprint="e", content=b"1"), ttl=60)
    await backend.set("b", CacheEntry(fingerprint="e", content=b"2"))

    try:
        await backend.cleanup()

        assert sorted(backend.cache) == ["a", "b"]
    finally:
        backend.stop_cleanup()


@pytest.mark.asyncio
async def test_memory_set_if_absent_stores_only_the_first_value(
    memory_backend: MemoryBackend,
):
    first = CacheEntry(fingerprint="lock", content=b"owner-a")
    second = CacheEntry(fingerprint="lock", content=b"owner-b")

    assert await memory_backend.set_if_absent("slot", first, 60) is True
    assert await memory_backend.set_if_absent("slot", second, 60) is False
    assert await memory_backend.get("slot") == first
    assert memory_backend.cache["slot"].expiry is not None


@pytest.mark.asyncio
async def test_memory_set_if_absent_without_ttl_never_expires(
    memory_backend: MemoryBackend,
):
    entry = CacheEntry(fingerprint="lock", content=b"owner-a")

    assert await memory_backend.set_if_absent("slot", entry) is True
    assert memory_backend.cache["slot"].expiry is None


@pytest.mark.asyncio
async def test_memory_set_if_absent_treats_an_expired_entry_as_absent(
    memory_backend: MemoryBackend,
):
    stale = CacheEntry(fingerprint="lock", content=b"owner-a")
    fresh = CacheEntry(fingerprint="lock", content=b"owner-b")
    await memory_backend.set("slot", stale, 60)
    memory_backend.cache["slot"].expiry = time.time() - 1

    assert await memory_backend.set_if_absent("slot", fresh, 60) is True
    assert await memory_backend.get("slot") == fresh


@pytest.mark.asyncio
async def test_memory_set_if_absent_has_exactly_one_winner(
    memory_backend: MemoryBackend,
):
    results = await asyncio.gather(
        *(
            memory_backend.set_if_absent(
                "slot", CacheEntry(fingerprint="lock", content=str(i).encode()), 60
            )
            for i in range(20)
        )
    )

    assert results.count(True) == 1
    winner = results.index(True)
    assert await memory_backend.get("slot") == CacheEntry(
        fingerprint="lock", content=str(winner).encode()
    )


@pytest.mark.asyncio
async def test_memory_delete_if_equals_removes_only_a_matching_entry(
    memory_backend: MemoryBackend,
):
    mine = CacheEntry(fingerprint="lock", content=b"owner-a")
    theirs = CacheEntry(fingerprint="lock", content=b"owner-b")
    await memory_backend.set("slot", theirs, 60)

    assert await memory_backend.delete_if_equals("slot", mine) is False
    assert await memory_backend.get("slot") == theirs

    assert await memory_backend.delete_if_equals("slot", theirs) is True
    assert "slot" not in memory_backend.cache
    assert await memory_backend.delete_if_equals("slot", theirs) is False


@pytest.mark.asyncio
async def test_memory_delete_if_equals_ignores_an_expired_entry(
    memory_backend: MemoryBackend,
):
    entry = CacheEntry(fingerprint="lock", content=b"owner-a")
    await memory_backend.set("slot", entry, 60)
    memory_backend.cache["slot"].expiry = time.time() - 1

    assert await memory_backend.delete_if_equals("slot", entry) is False


@pytest.mark.asyncio
async def test_memory_delete_if_equals_matches_a_counter(
    memory_backend: MemoryBackend,
):
    await memory_backend.increment("hits", 3)

    assert await memory_backend.delete_if_equals("hits", counter_entry(2)) is False
    assert await memory_backend.delete_if_equals("hits", counter_entry(3)) is True
    assert await memory_backend.get("hits") is None


@pytest.mark.asyncio
async def test_memory_lock_release_after_expiry_keeps_the_new_holder(
    memory_backend: MemoryBackend,
):
    """The lock cycle issue #62 asks for: a late release must not free a slot
    that has since been claimed by someone else."""
    owner_a = CacheEntry(fingerprint="lock", content=b"owner-a")
    owner_b = CacheEntry(fingerprint="lock", content=b"owner-b")

    assert await memory_backend.set_if_absent("slot", owner_a, 60) is True
    memory_backend.cache["slot"].expiry = time.time() - 1
    assert await memory_backend.set_if_absent("slot", owner_b, 60) is True

    assert await memory_backend.delete_if_equals("slot", owner_a) is False
    assert await memory_backend.get("slot") == owner_b


@pytest.mark.asyncio
async def test_memory_expire_if_equals_updates_ttl_only_when_matching(
    memory_backend: MemoryBackend,
):
    mine = CacheEntry(fingerprint="lock", content=b"owner-a")
    theirs = CacheEntry(fingerprint="lock", content=b"owner-b")
    await memory_backend.set("slot", theirs, 30)

    assert await memory_backend.expire_if_equals("slot", mine, 60) is False
    assert await memory_backend.expire_if_equals("slot", theirs, 60) is True
    assert memory_backend.cache["slot"].expiry is not None
    assert await memory_backend.expire_if_equals("missing", theirs, 60) is False


@pytest.mark.asyncio
async def test_memory_expire_if_equals_ignores_an_expired_entry(
    memory_backend: MemoryBackend,
):
    entry = CacheEntry(fingerprint="lock", content=b"owner-a")
    await memory_backend.set("slot", entry, 60)
    memory_backend.cache["slot"].expiry = time.time() - 1

    assert await memory_backend.expire_if_equals("slot", entry, 60) is False
