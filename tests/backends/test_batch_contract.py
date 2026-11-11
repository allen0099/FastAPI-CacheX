"""`get_many` and `set_many` read and write the same on every backend (#249).

The base class's loop over `get`/`set` (the `fallback` backend here) and the
batched overrides of the built-in backends must agree on what comes back.
"""

from collections.abc import AsyncIterator
from collections.abc import Callable

import pytest
import pytest_asyncio

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry
from tests.backends.test_base import DictBackend
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import memcached_skip_reason
from tests.live_servers import redis_skip_reason


def _redis() -> BaseCacheBackend:
    return AsyncRedisCacheBackend(
        host=REDIS_HOST, port=REDIS_PORT, key_prefix="cachex-test-batch:"
    )


def _memcached() -> BaseCacheBackend:
    return MemcachedBackend(servers=[MEMCACHED_SERVER])


FACTORIES: dict[
    str, tuple[Callable[[], BaseCacheBackend], Callable[[], str | None]]
] = {
    "memory": (MemoryBackend, lambda: None),
    "fallback": (DictBackend, lambda: None),
    "redis": (_redis, redis_skip_reason),
    "memcached": (_memcached, memcached_skip_reason),
}
# The last two are keys Memcached refuses as they are (a space, over 250 bytes).
KEYS = ("batch-a", "batch-b", "batch-c", "batch with space", "batch-" + "k" * 300)
A = CacheEntry(fingerprint="etag-a", content=b"a")
B = CacheEntry(fingerprint="etag-b", content=b"b")


@pytest_asyncio.fixture(params=list(FACTORIES))
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[BaseCacheBackend]:
    factory, skip_reason = FACTORIES[request.param]
    reason = skip_reason()
    if reason is not None:
        pytest.skip(reason)
    instance = factory()
    await instance.delete_many(list(KEYS))
    yield instance
    await instance.delete_many(list(KEYS))


async def test_set_many_then_get_many_round_trips(backend: BaseCacheBackend) -> None:
    entries = {key: CacheEntry(fingerprint=key, content=b"v") for key in KEYS}

    await backend.set_many(entries, ttl=60)

    assert await backend.get_many(KEYS) == entries
    # Readable one by one, so both paths name the keys the same way.
    for key, entry in entries.items():
        assert await backend.get(key) == entry


async def test_get_many_leaves_out_missing_keys(backend: BaseCacheBackend) -> None:
    await backend.set(KEYS[0], A)

    assert await backend.get_many([KEYS[1], KEYS[0], KEYS[2]]) == {KEYS[0]: A}
    assert await backend.get_many([KEYS[1]]) == {}


async def test_get_many_reads_a_repeated_key_once(backend: BaseCacheBackend) -> None:
    await backend.set_many({KEYS[0]: A, KEYS[1]: B})

    found = await backend.get_many([KEYS[0], KEYS[1], KEYS[0]])

    assert found == {KEYS[0]: A, KEYS[1]: B}


async def test_get_many_takes_any_iterable(backend: BaseCacheBackend) -> None:
    await backend.set(KEYS[0], A)

    assert await backend.get_many(key for key in KEYS[:2]) == {KEYS[0]: A}


async def test_set_many_overwrites_and_leaves_other_keys(
    backend: BaseCacheBackend,
) -> None:
    await backend.set_many({KEYS[0]: A, KEYS[2]: A})

    await backend.set_many({KEYS[0]: B, KEYS[1]: B})

    assert await backend.get_many(KEYS[:3]) == {KEYS[0]: B, KEYS[1]: B, KEYS[2]: A}


async def test_empty_batches_are_no_ops(backend: BaseCacheBackend) -> None:
    await backend.set_many({})
    await backend.set_many({}, ttl=60)

    assert await backend.get_many([]) == {}


async def test_get_many_reads_a_counter_like_get(backend: BaseCacheBackend) -> None:
    await backend.increment(KEYS[0], 3)

    assert await backend.get_many([KEYS[0]]) == {KEYS[0]: counter_entry(3)}
