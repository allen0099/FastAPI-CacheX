"""Redis cache backend implementation."""

import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.backends.codec import encode_entry
from fastapi_cachex.backends.config import (
    DEFAULT_REDIS_PREFIX as DEFAULT_REDIS_PREFIX,  # noqa: PLC0414
)
from fastapi_cachex.backends.config import RedisConfig
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import CACHE_KEY_SEPARATOR
from fastapi_cachex.types import CacheEntry

from .base import BaseCacheBackend

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)

# SCAN page size and DEL batch size; keeps individual commands small.
_BATCH_SIZE = 100

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
            encoding: Character encoding to use
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

    async def _scan_keys(self, pattern: str) -> list[str]:
        """Collect every key matching ``pattern`` (a full, prefixed glob).

        Uses SCAN instead of KEYS so the server is never blocked.
        """
        cursor = 0
        keys: list[str] = []
        while True:
            cursor, page = await self.client.scan(
                cursor, match=pattern, count=_BATCH_SIZE
            )
            keys.extend(page)
            if cursor == 0:
                return keys

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
        await self.client.set(self._make_key(key), encode_entry(value), ex=ttl)
        logger.debug("Redis SET; key=%s ttl=%s", key, ttl)

    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""
        await self.client.delete(self._make_key(key))
        logger.debug("Redis DELETE; key=%s", key)

    async def increment(self, key: str, delta: int = 1, ttl: int | None = None) -> int:
        """Atomically add ``delta`` to the counter at ``key`` (see base class).

        A short Lua script makes the increment and the expiry one server-side
        operation; the key is stored as a plain Redis integer.
        """
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
        removed = await self._delete_keys(await self._scan_keys(f"{self.key_prefix}*"))
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
        # after the path, so keys with no query params end with "|||".
        suffix = "*" if include_params else ""
        pattern = f"{self.key_prefix}*{CACHE_KEY_SEPARATOR}{path}{CACHE_KEY_SEPARATOR}{suffix}"
        keys = await self._scan_keys(pattern)

        # Also match direct keys (custom key formats without separators)
        # e.g. key_prefix + "gitlab:template" stored directly via backend.set()
        direct_key = self._make_key(path)
        if await self.client.exists(direct_key):
            keys.append(direct_key)

        cleared_count = await self._delete_keys(keys)
        logger.debug(
            "Redis CLEAR_PATH; path=%s include_params=%s removed=%s",
            path,
            include_params,
            cleared_count,
        )
        return cleared_count

    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern.

        Args:
            pattern: A glob pattern to match cache keys against

        Returns:
            Number of cache entries cleared
        """
        full_pattern = (
            pattern if pattern.startswith(self.key_prefix) else self._make_key(pattern)
        )
        cleared_count = await self._delete_keys(await self._scan_keys(full_pattern))
        logger.debug(
            "Redis CLEAR_PATTERN; pattern=%s removed=%s", full_pattern, cleared_count
        )
        return cleared_count

    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Returns:
            List of logical cache keys (without the backend key prefix)
        """
        keys = await self._scan_keys(f"{self.key_prefix}*")
        logical_keys = [k.removeprefix(self.key_prefix) for k in keys]
        logger.debug("Redis GET_ALL_KEYS; count=%s", len(logical_keys))
        return logical_keys

    async def get_cache_data(self) -> dict[str, tuple[CacheEntry, float | None]]:
        """Get all cache data with expiry information.

        Returns:
            Dictionary mapping cache keys to (CacheEntry, expiry) tuples.
            Note: Redis stores TTL but not absolute expiry time, so this
            returns None for expiry (no expiry tracking in Redis backend).
        """
        all_keys = await self.get_all_keys()
        cache_data: dict[str, tuple[CacheEntry, float | None]] = {}

        if not all_keys:
            return cache_data

        # Fetch all values in a single pipeline round-trip instead of N+1 GETs
        pipe = self.client.pipeline()
        for key in all_keys:
            pipe.get(self._make_key(key))
        raw_values: list[str | None] = await pipe.execute()

        for key, raw in zip(all_keys, raw_values, strict=False):
            value = decode_entry(raw)
            if value is not None:
                cache_data[key] = (value, None)

        logger.debug("Redis GET_CACHE_DATA; keys=%s", len(cache_data))
        return cache_data
