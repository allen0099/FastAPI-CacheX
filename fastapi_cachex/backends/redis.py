"""Redis cache backend implementation."""

import codecs
import logging
import time
import warnings
from collections.abc import Iterable
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.backends.codec import encode_entry
from fastapi_cachex.backends.config import DEFAULT_REDIS_PREFIX as DEFAULT_REDIS_PREFIX  # noqa: PLC0414
from fastapi_cachex.backends.config import RedisConfig
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import CACHE_KEY_SEPARATOR
from fastapi_cachex.types import CacheEntry

from .base import BaseCacheBackend
from .base import validate_ttl
from .base import warn_if_path_shaped

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)

# PTTL replies that are not a remaining lifetime.
_PTTL_NO_EXPIRY = -1
_PTTL_MISSING = -2

# SCAN page size and DEL batch size; keeps individual commands small.
_BATCH_SIZE = 100

# Characters that are live in a Redis glob pattern.
_GLOB_SPECIAL = frozenset("*?[]\\")


def _escape_glob(text: str) -> str:
    """Backslash-escape ``text`` so a Redis glob pattern matches it literally."""
    return "".join(f"\\{ch}" if ch in _GLOB_SPECIAL else ch for ch in text)


# INCRBY that attaches a TTL only when it creates the key, so a counter lives in
# a fixed window. KEYS[1] = key, ARGV[1] = delta, ARGV[2] = ttl (0 = none).
_INCREMENT_SCRIPT = """
local created = redis.call('EXISTS', KEYS[1]) == 0
local value = redis.call('INCRBY', KEYS[1], ARGV[1])
if created and tonumber(ARGV[2]) > 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return value
"""

# DEL that only fires while the key still holds the exact bytes the caller read,
# so a value replaced in the meantime survives. KEYS[1] = key, ARGV[1] = bytes.
_DELETE_IF_EQUALS_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

# EXPIRE that only fires while the key still holds the exact bytes the caller read,
# so a value replaced in the meantime survives. KEYS[1] = key, ARGV[1] = bytes, ARGV[2] = ttl.
_EXPIRE_IF_EQUALS_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


def _warn_if_not_utf8(encoding: str) -> None:
    r"""Warn when replies would be decoded with anything but UTF-8.

    The shared codec writes UTF-8 JSON, and the client decodes each reply with
    ``encoding``, so under e.g. latin-1 a stored ``b"\xe9"`` reads back as
    ``b"\xc3\xa9"``. Aliases such as ``"UTF8"`` and ``"utf_8"`` are accepted.
    """
    try:
        name = codecs.lookup(encoding).name
    except LookupError:
        return  # the client rejects an unknown encoding itself
    if name != "utf-8":
        warnings.warn(
            f"AsyncRedisCacheBackend(encoding={encoding!r}) will corrupt non-ASCII "
            "cached content: entries are always written as UTF-8, and replies "
            "are decoded with this encoding. Use encoding='utf-8'. The encoding "
            "parameter will be removed in version 0.4.0.",
            RuntimeWarning,
            stacklevel=3,
        )


class AsyncRedisCacheBackend(BaseCacheBackend):
    """Async Redis cache backend implementation.

    This backend uses Redis with a key prefix to avoid conflicts with other
    applications. Keys are namespaced with 'fastapi_cachex:' by default.
    """

    client: "AsyncRedis[str]"
    key_prefix: str

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: str | None = None,
        db: int = 0,
        encoding: str = "utf-8",
        decode_responses: Literal[True] = True,
        socket_timeout: float = 1.0,
        socket_connect_timeout: float = 1.0,
        key_prefix: str = DEFAULT_REDIS_PREFIX,
        protocol: int = 2,
        **kwargs: Any,
    ) -> None:
        """Initialize async Redis cache backend.

        Args:
            host: Redis host
            port: Redis port
            password: Redis password
            db: Redis database number
            encoding: Character encoding the client decodes replies with.
                Leave it as UTF-8: entries are always written as UTF-8 JSON, so
                any other encoding corrupts non-ASCII content on the way back,
                and a ``RuntimeWarning`` says so. The parameter will be removed
                in 0.4.0.
            decode_responses: Whether to decode response automatically
            socket_timeout: Timeout for socket operations (in seconds)
            socket_connect_timeout: Timeout for socket connection (in seconds)
            key_prefix: Prefix for all cache keys (default: 'fastapi_cachex:')
            protocol: RESP protocol version (2 or 3). Defaults to 2 (RESP2) for
                broadest compatibility. Use 3 only when hiredis >= 3.0 is installed
                and Redis 8.0+ RESP3 features are required.
            **kwargs: Additional arguments to pass to Redis client
        """
        try:
            # Import top-level package first so tests that monkeypatch
            # builtins.__import__("redis") can simulate absence reliably.
            import redis  # noqa: F401
            from redis.asyncio import Redis as AsyncRedis
        except ImportError:
            msg = (
                "redis[hiredis] is not installed. Please install it with "
                "'pip install \"redis[hiredis]\"' "
            )
            raise CacheXError(msg)

        _warn_if_not_utf8(encoding)

        # `protocol` is not in the types-redis stubs (added in redis-py 5.x).
        # Pass it via **kwargs so mypy doesn't complain about an unknown keyword.
        kwargs.setdefault("protocol", protocol)
        self.client = AsyncRedis(
            host=host,
            port=port,
            password=password,
            db=db,
            encoding=encoding,
            decode_responses=decode_responses,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            **kwargs,
        )
        self.key_prefix = key_prefix
        # Registered once so every call is an EVALSHA (redis-py reloads the
        # script transparently if the server has flushed it).
        self._increment_script = self.client.register_script(_INCREMENT_SCRIPT)
        self._delete_if_equals_script = self.client.register_script(
            _DELETE_IF_EQUALS_SCRIPT
        )
        self._expire_if_equals_script = self.client.register_script(
            _EXPIRE_IF_EQUALS_SCRIPT
        )

    @staticmethod
    def load_from_config(config: RedisConfig) -> "AsyncRedisCacheBackend":
        """Create AsyncRedisCacheBackend from RedisConfig.

        Args:
            config: RedisConfig instance
        Returns:
            An instance of AsyncRedisCacheBackend
        """
        return AsyncRedisCacheBackend(
            host=config.host,
            port=config.port,
            password=config.password.get_secret_value()
            if config.password is not None
            else None,
            db=config.db,
            encoding=config.encoding,
            socket_timeout=config.socket_timeout,
            socket_connect_timeout=config.socket_connect_timeout,
            key_prefix=config.key_prefix,
            protocol=config.protocol,
        )

    def _make_key(self, key: str) -> str:
        """Add prefix to cache key."""
        return f"{self.key_prefix}{key}"

    @property
    def _prefix_pattern(self) -> str:
        """The key prefix as a literal glob, so ``*``/``?``/``[`` in it stay inert."""
        return _escape_glob(self.key_prefix)

    async def _scan_keys(self, pattern: str) -> list[str]:
        """Collect every key matching ``pattern`` (a full, prefixed glob).

        Uses SCAN instead of KEYS so the server is never blocked. SCAN may
        return a key more than once (when the keyspace shrinks mid-iteration),
        so the result is deduplicated, keeping the order keys were first seen.
        """
        cursor = 0
        keys: dict[str, None] = {}
        while True:
            cursor, page = await self.client.scan(
                cursor, match=pattern, count=_BATCH_SIZE
            )
            keys.update(dict.fromkeys(page))
            if cursor == 0:
                return list(keys)

    async def _delete_matching(self, pattern: str) -> int:
        """Delete every key matching ``pattern``, one SCAN page at a time.

        Each page is deleted as it arrives, so the keyspace is never held in
        memory. Deleting keys SCAN already returned is safe: SCAN still returns
        every key present for the whole iteration. A key SCAN repeats is
        already gone by then, and DEL counts only keys that existed, so it is
        not counted twice.

        Returns:
            How many keys were deleted
        """
        cursor = 0
        deleted = 0
        while True:
            cursor, page = await self.client.scan(
                cursor, match=pattern, count=_BATCH_SIZE
            )
            if page:
                deleted += await self.client.delete(*page)
            if cursor == 0:
                return deleted

    async def _delete_keys(self, keys: list[str]) -> int:
        """Delete prefixed keys in batches; returns how many existed."""
        deleted = 0
        for i in range(0, len(keys), _BATCH_SIZE):
            deleted += await self.client.delete(*keys[i : i + _BATCH_SIZE])
        return deleted

    async def get(self, key: str) -> CacheEntry | None:
        """Retrieve a cached response."""
        value = decode_entry(await self.client.get(self._make_key(key)))
        logger.debug("Redis %s; key=%s", "HIT" if value else "MISS", key)
        return value

    async def set(self, key: str, value: CacheEntry, ttl: int | None = None) -> None:
        """Store a response in the cache."""
        validate_ttl(ttl)
        await self.client.set(self._make_key(key), encode_entry(value), ex=ttl)
        logger.debug("Redis SET; key=%s ttl=%s", key, ttl)

    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""
        await self.client.delete(self._make_key(key))
        logger.debug("Redis DELETE; key=%s", key)

    async def delete_many(self, keys: Iterable[str]) -> int:
        """Remove every key in ``keys`` with batched DELs; returns how many existed."""
        removed = await self._delete_keys([self._make_key(key) for key in keys])
        logger.debug("Redis DELETE_MANY; removed=%s", removed)
        return removed

    async def get_and_delete(self, key: str) -> CacheEntry | None:
        """Atomically retrieve and remove a cached entry (see base class).

        Uses GETDEL, which requires Redis server 6.2 or newer.
        """
        value = decode_entry(await self.client.getdel(self._make_key(key)))
        logger.debug("Redis GETDEL %s; key=%s", "HIT" if value else "MISS", key)
        return value

    async def set_if_absent(
        self, key: str, value: CacheEntry, ttl: int | None = None
    ) -> bool:
        """Atomically store ``value`` unless ``key`` exists (see base class).

        A single ``SET ... NX EX``.
        """
        validate_ttl(ttl)
        stored = await self.client.set(
            self._make_key(key), encode_entry(value), ex=ttl, nx=True
        )
        logger.debug(
            "Redis SET_IF_ABSENT %s; key=%s ttl=%s",
            "STORED" if stored else "EXISTS",
            key,
            ttl,
        )
        return bool(stored)

    async def delete_if_equals(self, key: str, expected: CacheEntry) -> bool:
        """Atomically remove ``key`` while it holds ``expected`` (see base class).

        The stored value is decoded and compared here, then a Lua script
        deletes the key only if it still holds the bytes that were compared,
        so a value written in between is never removed.
        """
        prefixed_key = self._make_key(key)
        raw = await self.client.get(prefixed_key)
        if raw is None or decode_entry(raw) != expected:
            logger.debug("Redis DELETE_IF_EQUALS MISMATCH; key=%s", key)
            return False
        deleted = await self._delete_if_equals_script(keys=[prefixed_key], args=[raw])
        logger.debug(
            "Redis DELETE_IF_EQUALS %s; key=%s", "HIT" if deleted else "LOST RACE", key
        )
        return bool(deleted)

    async def expire_if_equals(self, key: str, expected: CacheEntry, ttl: int) -> bool:
        """Atomically update expiry on ``key`` while it holds ``expected`` (see base class).

        The stored value is decoded and compared here, then a Lua script
        updates the expiry on the key only if it still holds the bytes that
        were compared, so a value written in between is never overwritten.
        """
        validate_ttl(ttl)
        prefixed_key = self._make_key(key)
        raw = await self.client.get(prefixed_key)
        if raw is None or decode_entry(raw) != expected:
            logger.debug("Redis EXPIRE_IF_EQUALS MISMATCH; key=%s", key)
            return False
        updated = await self._expire_if_equals_script(
            keys=[prefixed_key], args=[raw, ttl]
        )
        logger.debug(
            "Redis EXPIRE_IF_EQUALS %s; key=%s ttl=%s",
            "HIT" if updated else "LOST RACE",
            key,
            ttl,
        )
        return bool(updated)

    async def increment(self, key: str, delta: int = 1, ttl: int | None = None) -> int:
        """Atomically add ``delta`` to the counter at ``key`` (see base class).

        A short Lua script makes the increment and the expiry one server-side
        operation; the key is stored as a plain Redis integer.
        """
        validate_ttl(ttl)
        from redis.exceptions import ResponseError

        try:
            value = await self._increment_script(
                keys=[self._make_key(key)], args=[delta, ttl or 0]
            )
        except ResponseError as e:
            if "not an integer" not in str(e):
                raise
            msg = "Cache key holds a value that is not a counter"
            raise CacheXError(msg) from e
        logger.debug("Redis INCREMENT; key=%s value=%s ttl=%s", key, value, ttl)
        return int(value)

    async def clear(self) -> None:
        """Clear all cached responses for this namespace.

        Only deletes keys within this backend's prefix.
        """
        removed = await self._delete_matching(f"{self._prefix_pattern}*")
        logger.debug("Redis CLEAR; removed=%s", removed)

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path.

        Args:
            path: The path to clear cache for
            include_params: Whether to clear all parameter variations

        Returns:
            Number of cache entries cleared
        """
        # Keys are method|||host|||path|||query. Without include_params only the
        # exact path is matched: default_key_builder always appends a separator
        # after the path, so keys with no query params end with "|||". The
        # path is a literal, not a glob: "/files/[draft]" means those brackets.
        suffix = "*" if include_params else ""
        pattern = (
            f"{self._prefix_pattern}*{CACHE_KEY_SEPARATOR}"
            f"{_escape_glob(path)}{CACHE_KEY_SEPARATOR}{suffix}"
        )
        cleared_count = await self._delete_matching(pattern)

        # Also match direct keys (custom key formats without separators)
        # e.g. key_prefix + "gitlab:template" stored directly via backend.set().
        # DEL returns 0 for a missing key, so no EXISTS check is needed.
        cleared_count += await self.client.delete(self._make_key(path))
        logger.debug(
            "Redis CLEAR_PATH; path=%s include_params=%s removed=%s",
            path,
            include_params,
            cleared_count,
        )
        return cleared_count

    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern.

        Only ``pattern`` is a live glob; the backend's key prefix is matched
        literally and always added, so ``pattern`` matches the logical key like
        on every other backend.

        Before 0.3.8 a pattern that started with the key prefix was matched
        with the prefix stripped instead. When a pattern like that clears
        nothing, the old form is still tried, and a ``DeprecationWarning`` is
        emitted if it clears anything. That retry will be removed in 0.4.0.

        Args:
            pattern: A glob pattern to match cache keys against

        Returns:
            Number of cache entries cleared
        """
        full_pattern = self._prefix_pattern + pattern
        cleared_count = await self._delete_matching(full_pattern)
        if (
            cleared_count == 0
            and self.key_prefix
            and pattern.startswith(self.key_prefix)
        ):
            full_pattern = self._prefix_pattern + pattern.removeprefix(self.key_prefix)
            cleared_count = await self._delete_matching(full_pattern)
            if cleared_count:
                warnings.warn(
                    f"clear_pattern({pattern!r}) matched only with the backend's "
                    f"key prefix {self.key_prefix!r} stripped. Patterns match the "
                    "logical key, without the backend prefix; pass "
                    f"{pattern.removeprefix(self.key_prefix)!r} instead. The "
                    "stripped retry will be removed in version 0.4.0.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        warn_if_path_shaped(pattern, cleared_count)
        logger.debug(
            "Redis CLEAR_PATTERN; pattern=%s removed=%s", full_pattern, cleared_count
        )
        return cleared_count

    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Returns:
            List of logical cache keys (without the backend key prefix)
        """
        keys = await self._scan_keys(f"{self._prefix_pattern}*")
        logical_keys = [k.removeprefix(self.key_prefix) for k in keys]
        logger.debug("Redis GET_ALL_KEYS; count=%s", len(logical_keys))
        return logical_keys

    async def get_cache_data(self) -> dict[str, tuple[CacheEntry, float | None]]:
        """Get all cache data with expiry information.

        Returns:
            Dictionary mapping cache keys to (CacheEntry, expiry) tuples, where
            expiry is an absolute ``time.time()`` timestamp like the memory
            backend reports, or None for a key without a TTL. It is derived
            from each key's ``PTTL``, so it is accurate to the round-trip.
        """
        all_keys = await self.get_all_keys()
        cache_data: dict[str, tuple[CacheEntry, float | None]] = {}

        # Fetch values and remaining lifetimes in pipelines of _BATCH_SIZE
        # keys instead of 2N round trips. The pipelines are not transactional:
        # a MULTI/EXEC over the whole keyspace would block the server for its
        # duration, and a consistent snapshot is not needed for monitoring.
        for start in range(0, len(all_keys), _BATCH_SIZE):
            chunk = all_keys[start : start + _BATCH_SIZE]
            pipe = self.client.pipeline(transaction=False)
            for key in chunk:
                redis_key = self._make_key(key)
                pipe.get(redis_key)
                pipe.pttl(redis_key)
            replies: list[Any] = await pipe.execute()
            now = time.time()

            for key, raw, pttl in zip(chunk, replies[::2], replies[1::2], strict=True):
                # -2: the key expired or was deleted between SCAN and this fetch.
                if pttl == _PTTL_MISSING:
                    continue
                value = decode_entry(raw)
                if value is None:
                    continue
                expiry = None if pttl == _PTTL_NO_EXPIRY else now + pttl / 1000
                cache_data[key] = (value, expiry)

        logger.debug("Redis GET_CACHE_DATA; keys=%s", len(cache_data))
        return cache_data
