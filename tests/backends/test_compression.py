"""``compress_min_size`` on the Redis and Memcached backends (#560).

The codec is tested on its own; the backends, against live servers, through
every primitive that writes or compares an entry.
"""

import json as std_json
import os
import types
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any

import pytest
import pytest_asyncio

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends import codec
from fastapi_cachex.backends.config import RedisConfig
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.exceptions import ValueTooLargeError
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import memcached_skip_reason
from tests.live_servers import redis_skip_reason

MIN_SIZE = 1024
BIG = CacheEntry(
    fingerprint="etag-big",
    content=b'{"name": "value", "list": [1, 2, 3]}' * 200,
    media_type="application/json",
    headers=(("x-a", "1"),),
    stored_at=1.5,
    tags=(("t", "v1"),),
    stale_at=2.5,
)
SMALL = CacheEntry(fingerprint="etag-small", content=b"small")


def test_a_document_at_the_threshold_is_compressed_and_reads_back() -> None:
    plain = codec.encode_entry(BIG)

    encoded = codec.encode_entry(BIG, len(plain))

    assert encoded.startswith(codec.COMPRESSED_PREFIX)
    assert len(encoded) < len(plain) / 4
    assert codec.decode_entry(encoded) == BIG
    # One byte short of the threshold: stored as before.
    assert codec.encode_entry(BIG, len(plain) + 1) == plain
    assert codec.encode_entry(BIG, None) == plain


def test_random_bytes_shrink_too() -> None:
    # As JSON text a byte above 0x7f takes two bytes or an escape.
    noise = CacheEntry(fingerprint="etag", content=os.urandom(100_000))

    encoded = codec.encode_entry(noise, 1)

    assert len(encoded) < len(codec.encode_entry(noise))
    assert codec.decode_entry(encoded) == noise


def test_compression_works_without_orjson(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        codec,
        "json",
        types.SimpleNamespace(dumps=std_json.dumps, loads=std_json.loads),
    )

    encoded = codec.encode_entry(BIG, 1)

    assert encoded.startswith(codec.COMPRESSED_PREFIX)
    assert codec.decode_entry(encoded) == BIG


def test_a_document_over_the_bound_is_neither_compressed_nor_inflated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = codec.encode_entry(BIG, 1)
    plain = codec.encode_entry(BIG)

    monkeypatch.setattr(codec, "_MAX_COMPRESSED_DOCUMENT", len(plain) - 1)

    assert codec.decode_entry(encoded) is None
    assert codec.encode_entry(BIG, 1) == plain
    monkeypatch.setattr(codec, "_MAX_COMPRESSED_DOCUMENT", len(plain))
    assert codec.decode_entry(encoded) == BIG
    assert codec.encode_entry(BIG, 1) == encoded


def test_a_counter_is_never_compressed() -> None:
    assert codec.encode_entry(counter_entry(12345), 1) == b"12345"


@pytest.mark.parametrize(
    "raw",
    [
        codec.COMPRESSED_PREFIX,
        codec.COMPRESSED_PREFIX + b"not zlib",
        codec.encode_entry(BIG, 1)[:-5],
        codec.COMPRESSED_PREFIX + b"x\x01\x03\x00\x00\x00\x00\x01",  # empty
        codec.encode_entry(BIG, 1) + b"junk",
    ],
)
def test_a_damaged_compressed_document_is_a_miss(raw: bytes) -> None:
    assert codec.decode_entry(raw) is None


@pytest.mark.parametrize("value", [0, -1])
def test_compress_min_size_must_be_positive(value: int) -> None:
    with pytest.raises(ValueError, match="compress_min_size must be greater than"):
        MemcachedBackend(servers=[MEMCACHED_SERVER], compress_min_size=value)
    with pytest.raises(ValueError, match="compress_min_size must be greater than"):
        AsyncRedisCacheBackend(compress_min_size=value)
    with pytest.raises(ValueError, match="compress_min_size"):
        RedisConfig(compress_min_size=value)


@pytest.mark.parametrize("value", [True, 1.5, "1024"])
def test_compress_min_size_must_be_an_int(value: Any) -> None:
    with pytest.raises(TypeError, match="compress_min_size must be a number of bytes"):
        MemcachedBackend(servers=[MEMCACHED_SERVER], compress_min_size=value)
    with pytest.raises(TypeError, match="compress_min_size must be a number of bytes"):
        AsyncRedisCacheBackend(compress_min_size=value)


def test_the_config_takes_it_as_its_other_numbers() -> None:
    # Lax, like `port`: a config is often built from environment strings.
    assert RedisConfig(compress_min_size="4096").compress_min_size == 4096
    with pytest.raises(ValueError, match="compress_min_size"):
        RedisConfig(compress_min_size="4 kB")


def test_a_pool_that_decodes_replies_cannot_compress() -> None:
    from redis.asyncio import ConnectionPool

    pool = ConnectionPool(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    with pytest.raises(CacheXError, match="decode_responses=True"):
        AsyncRedisCacheBackend(connection_pool=pool, compress_min_size=1024)


async def test_from_config_passes_it_on() -> None:
    backend = AsyncRedisCacheBackend.load_from_config(
        RedisConfig(compress_min_size=2048)
    )

    assert backend.compress_min_size == 2048
    assert (
        AsyncRedisCacheBackend.load_from_config(RedisConfig()).compress_min_size is None
    )


def _redis(**kwargs: Any) -> AsyncRedisCacheBackend:
    return AsyncRedisCacheBackend(
        host=REDIS_HOST, port=REDIS_PORT, key_prefix="cachex-test-z:", **kwargs
    )


def _memcached(**kwargs: Any) -> MemcachedBackend:
    return MemcachedBackend(servers=[MEMCACHED_SERVER], **kwargs)


async def _raw_redis(backend: Any, key: str) -> bytes:
    raw = await backend.client.get(backend._make_key(key))
    assert isinstance(raw, bytes)
    return raw


async def _raw_memcached(backend: Any, key: str) -> bytes:
    raw = backend.client.get(backend._make_key(key))
    assert isinstance(raw, bytes)
    return raw


FACTORIES: dict[str, tuple[Callable[..., Any], Callable[[], str | None], Any]] = {
    "redis": (_redis, redis_skip_reason, _raw_redis),
    "memcached": (_memcached, memcached_skip_reason, _raw_memcached),
}
KEYS = ("z-a", "z-b")


class Live:
    """A compressing backend, one that does not compress, and the raw bytes."""

    def __init__(self, name: str) -> None:
        factory, _, self._raw = FACTORIES[name]
        self.compressing = factory(compress_min_size=MIN_SIZE)
        self.plain = factory()

    async def stored(self, key: str) -> bytes:
        raw: bytes = await self._raw(self.plain, key)
        return raw

    async def is_compressed(self, key: str) -> bool:
        return (await self.stored(key)).startswith(codec.COMPRESSED_PREFIX)


@pytest_asyncio.fixture(params=list(FACTORIES))
async def live(request: pytest.FixtureRequest) -> AsyncIterator[Live]:
    reason = FACTORIES[request.param][1]()
    if reason is not None:
        pytest.skip(reason)
    instance = Live(request.param)
    await instance.plain.delete_many(list(KEYS))
    yield instance
    await instance.plain.delete_many(list(KEYS))


async def test_set_compresses_a_large_entry_only(live: Live) -> None:
    await live.compressing.set("z-a", BIG, ttl=60)
    await live.compressing.set("z-b", SMALL, ttl=60)

    assert await live.is_compressed("z-a")
    assert len(await live.stored("z-a")) < len(codec.encode_entry(BIG)) / 4
    assert not await live.is_compressed("z-b")
    assert await live.compressing.get("z-a") == BIG
    assert await live.compressing.get("z-b") == SMALL


async def test_a_backend_that_does_not_compress_reads_and_replaces_one(
    live: Live,
) -> None:
    await live.compressing.set("z-a", BIG, ttl=60)

    assert await live.plain.get("z-a") == BIG
    assert await live.plain.get_many(["z-a"]) == {"z-a": BIG}
    # The compare-and-write primitives compare entries, not stored bytes.
    assert await live.plain.expire_if_equals("z-a", BIG, ttl=60)
    assert await live.plain.set_if_equals("z-a", BIG, SMALL, ttl=60)
    assert await live.compressing.set_if_equals("z-a", SMALL, BIG, ttl=60)
    assert await live.is_compressed("z-a")
    assert await live.plain.delete_if_equals("z-a", BIG)
    assert await live.plain.get("z-a") is None


async def test_every_write_compresses(live: Live) -> None:
    assert await live.compressing.set_if_absent("z-a", BIG, ttl=60)
    assert await live.is_compressed("z-a")

    await live.compressing.set_many({"z-b": BIG}, ttl=60)
    assert await live.is_compressed("z-b")
    assert await live.compressing.get_many(["z-a", "z-b"]) == {"z-a": BIG, "z-b": BIG}

    assert await live.compressing.get_and_delete("z-a") == BIG
    assert await live.compressing.get("z-a") is None


async def test_memcached_stores_an_entry_over_its_item_limit_once_compressed() -> None:
    reason = memcached_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    rows = b'{"id": 12345, "name": "a name", "tags": ["a", "b"]},' * 30000
    entry = CacheEntry(fingerprint="etag-huge", content=rows)
    assert len(codec.encode_entry(entry)) > 1024 * 1024
    try:
        with pytest.raises(ValueTooLargeError):
            await _memcached().set("z-a", entry, ttl=60)

        await _memcached(compress_min_size=MIN_SIZE).set("z-a", entry, ttl=60)

        assert await _memcached().get("z-a") == entry
    finally:
        await _memcached().delete("z-a")
