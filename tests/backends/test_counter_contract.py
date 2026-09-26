"""A counter is recognised the same way on every backend (#111).

The memory backend and the base fallback parsed any entry whose body was a
number as a counter, so `increment()` on a cached response with body `42`
returned 43 and overwrote it, while Redis and Memcached raised. And a counter
written with `set(key, counter_entry(n))` could be incremented only on the
memory backend, because the network backends stored it as a JSON document.
"""

from collections.abc import AsyncIterator
from collections.abc import Callable

import pytest
import pytest_asyncio

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import COUNTER_FINGERPRINT
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry
from fastapi_cachex.types import counter_value
from fastapi_cachex.types import parse_counter
from tests.backends.test_base import DictBackend
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import memcached_skip_reason
from tests.live_servers import redis_skip_reason


def _redis() -> BaseCacheBackend:
    return AsyncRedisCacheBackend(
        host=REDIS_HOST, port=REDIS_PORT, key_prefix="cachex-test-counter:"
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
KEYS = ("counter-contract-page", "counter-contract-hits")


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


@pytest.mark.asyncio
async def test_increment_rejects_a_cached_response_with_a_numeric_body(
    backend: BaseCacheBackend,
) -> None:
    page = CacheEntry(fingerprint="etag", content=b"42")
    await backend.set(KEYS[0], page)

    with pytest.raises(CacheXError, match="not a counter"):
        await backend.increment(KEYS[0])

    assert await backend.get(KEYS[0]) == page


@pytest.mark.asyncio
async def test_increment_continues_a_counter_written_with_set(
    backend: BaseCacheBackend,
) -> None:
    await backend.set(KEYS[1], counter_entry(5))

    assert await backend.increment(KEYS[1]) == 6
    assert await backend.increment(KEYS[1], -2) == 4  # Memcached stops at 0
    assert await backend.get(KEYS[1]) == counter_entry(4)


@pytest.mark.asyncio
async def test_a_counter_that_shrinks_reads_back_as_a_counter(
    backend: BaseCacheBackend,
) -> None:
    """Memcached pads the value DECR shortens ("10" -> "9 ")."""
    await backend.increment(KEYS[1], 10)
    await backend.increment(KEYS[1], -1)

    assert await backend.get(KEYS[1]) == counter_entry(9)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("7", 7),
        (b"-7", -7),
        (b"9 ", 9),
        (b" 7", None),
        (b"7\n", None),
        (b"1_0", None),
        (b"+7", None),
        (chr(0x0667), None),  # ARABIC-INDIC DIGIT SEVEN, which int() accepts
        (b"", None),
        (b"-", None),
    ],
)
def test_parse_counter_accepts_only_what_the_servers_write(
    raw: str | bytes, expected: int | None
) -> None:
    assert parse_counter(raw) == expected


@pytest.mark.parametrize(
    "entry",
    [
        CacheEntry(fingerprint="etag", content=b"42"),
        CacheEntry(fingerprint=COUNTER_FINGERPRINT, content=b"1_0"),
    ],
)
def test_counter_value_requires_the_counter_fingerprint_and_a_plain_integer(
    entry: CacheEntry,
) -> None:
    with pytest.raises(CacheXError, match="not a counter"):
        counter_value(entry)
