"""Memcached cache backend implementation."""

import asyncio
import logging
import warnings

from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.backends.codec import encode_entry
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import CacheEntry

from .base import BaseCacheBackend

logger = logging.getLogger(__name__)

# Default Memcached key prefix for fastapi-cachex
DEFAULT_MEMCACHE_PREFIX = "fastapi_cachex:"


class MemcachedBackend(BaseCacheBackend):
    """Memcached backend implementation.

    Note: This implementation uses the synchronous pymemcache client and runs
    each call in a worker thread. The client is connection-pooled so concurrent
    calls never share a socket. For true async Memcached operations consider
    aiomcache. Keys are namespaced with 'fastapi_cachex:' by default to avoid
    conflicts with other applications.

    Limitations:
    - Pattern-based clearing (clear_pattern) is not supported by Memcached protocol
    - Operations are wrapped to appear async but use blocking sync client internally
    """

    key_prefix: str

    def __init__(
        self,
        servers: list[str],
        key_prefix: str = DEFAULT_MEMCACHE_PREFIX,
    ) -> None:
        """Initialize the Memcached backend.

        Args:
            servers: List of Memcached servers in format ["host:port", ...]
            key_prefix: Prefix for all cache keys (default: 'fastapi_cachex:')

        Raises:
            CacheXError: If pymemcache is not installed
        """
        try:
            from pymemcache import HashClient
        except ImportError:
            msg = "pymemcache is not installed. Please install it with 'pip install pymemcache'"
            raise CacheXError(msg)

        self.client = HashClient(
            servers, connect_timeout=5, timeout=5, use_pooling=True
        )
        self.key_prefix = key_prefix

    def _make_key(self, key: str) -> str:
        """Add prefix to cache key."""
        return f"{self.key_prefix}{key}"

    async def get(self, key: str) -> CacheEntry | None:
        """Get value from cache.

        Args:
            key: Cache key to retrieve

        Returns:
            Cached entry if found, None otherwise
        """
        raw = await asyncio.to_thread(self.client.get, self._make_key(key))
        value = decode_entry(raw)
        if raw is None:
            logger.debug("Memcached MISS; key=%s", key)
        elif value is None:
            logger.debug("Memcached DESERIALIZE ERROR; key=%s", key)
        else:
            logger.debug("Memcached HIT; key=%s", key)
        return value

    async def set(self, key: str, value: CacheEntry, ttl: int | None = None) -> None:
        """Set value in cache.

        Args:
            key: Cache key
            value: CacheEntry instance to store
            ttl: Time to live in seconds
        """
        expire = ttl if ttl is not None else 0
        await asyncio.to_thread(
            self.client.set, self._make_key(key), encode_entry(value), expire
        )
        logger.debug("Memcached SET; key=%s ttl=%s", key, ttl)

    def _add_delta(self, prefixed_key: str, delta: int) -> int | None:
        """Apply ``delta`` with INCR/DECR; ``None`` when the key does not exist."""
        if delta < 0:
            result = self.client.decr(prefixed_key, -delta, noreply=False)
        else:
            result = self.client.incr(prefixed_key, delta, noreply=False)
        return None if result is None else int(result)

    async def increment(self, key: str, delta: int = 1, ttl: int | None = None) -> int:
        """Atomically add ``delta`` to the counter at ``key`` (see base class).

        Memcached counters are unsigned, so a negative ``delta`` uses DECR,
        which stops at 0 instead of going negative.
        """
        from pymemcache.exceptions import MemcacheClientError

        prefixed_key = self._make_key(key)
        try:
            value = await asyncio.to_thread(self._add_delta, prefixed_key, delta)
            if value is None:
                # No counter yet: ADD is atomic and a no-op when a concurrent
                # call created it first, so the retry always finds a counter.
                await asyncio.to_thread(
                    self.client.add, prefixed_key, b"0", ttl or 0, noreply=False
                )
                value = await asyncio.to_thread(self._add_delta, prefixed_key, delta)
        except MemcacheClientError as e:
            msg = "Cache key holds a value that is not a counter"
            raise CacheXError(msg) from e
        if value is None:  # pragma: no cover - the counter expired mid-call
            msg = "Counter vanished between ADD and INCR"
            raise CacheXError(msg)
        logger.debug("Memcached INCREMENT; key=%s value=%s ttl=%s", key, value, ttl)
        return value

    async def delete(self, key: str) -> None:
        """Delete value from cache.

        Args:
            key: Cache key to delete
        """
        prefixed = self._make_key(key)
        await asyncio.to_thread(self.client.delete, prefixed)
        logger.debug("Memcached DELETE; key=%s", key)

    async def clear(self) -> None:
        """Clear all values from cache.

        Note: Memcached's flush_all affects the entire server.
        Consider using clear_path() with your specific keys instead.
        """
        warnings.warn(
            "Memcached.clear() flushes ALL cached data from the server, "
            "affecting other applications. Consider using clear_path() instead "
            "to selectively remove only this namespace's keys.",
            RuntimeWarning,
            stacklevel=2,
        )
        await asyncio.to_thread(self.client.flush_all)
        logger.debug("Memcached CLEAR; flush_all issued")

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path.

        Note: Memcached does not support pattern-based queries.
        This method can only delete keys if the exact key is provided,
        or will try to match keys in memory if include_params=True.
        For better pattern support, consider using Redis backend.

        Args:
            path: The path to clear cache for
            include_params: Currently unsupported (Memcached limitation)

        Returns:
            Number of cache entries cleared (0 or 1 for exact match only)
        """
        if include_params:
            warnings.warn(
                "Memcached backend does not support pattern-based key clearing. "
                "Only exact key matches can be deleted. "
                "The include_params option has no effect. "
                "Consider using Redis backend for pattern support.",
                RuntimeWarning,
                stacklevel=2,
            )

        # Try to delete the prefixed key (exact match only)
        prefixed_key = self._make_key(path)
        try:
            result = await asyncio.to_thread(
                self.client.delete, prefixed_key, noreply=False
            )
        except Exception:  # noqa: BLE001
            return 0
        else:
            logger.debug(
                "Memcached CLEAR_PATH; path=%s include_params=%s removed=%s",
                path,
                include_params,
                1 if result else 0,
            )
            return 1 if result else 0

    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern.

        Memcached does not support pattern matching or key scanning.
        This operation is not available.

        Args:
            pattern: A glob pattern (not supported by Memcached)

        Returns:
            Always 0, as pattern matching is not supported
        """
        warnings.warn(
            "Memcached backend does not support pattern matching. "
            "Pattern-based cache clearing is not available with Memcached. "
            "Consider using Redis backend for pattern support, "
            "or track keys manually in your application logic.",
            RuntimeWarning,
            stacklevel=2,
        )
        logger.debug("Memcached CLEAR_PATTERN unsupported; pattern=%s", pattern)
        return 0

    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Note: Memcached does not support key scanning directly.
        This returns an empty list as Memcached has no built-in way to enumerate keys.
        For key enumeration, consider using Redis backend or tracking keys
        manually in your application.

        Returns:
            Empty list (Memcached limitation)
        """
        warnings.warn(
            "Memcached backend does not support key enumeration. "
            "get_all_keys() returns an empty list. "
            "Consider using Redis backend if you need cache monitoring, "
            "or track keys manually in your application.",
            RuntimeWarning,
            stacklevel=2,
        )
        logger.debug("Memcached GET_ALL_KEYS unsupported; returning empty list")
        return []

    async def get_cache_data(self) -> dict[str, tuple[CacheEntry, float | None]]:
        """Get all cache data with expiry information.

        Note: Memcached does not support key enumeration or pattern matching.
        This method returns an empty dictionary.

        Returns:
            Empty dictionary (Memcached limitation)
        """
        warnings.warn(
            "Memcached backend does not support key enumeration. "
            "get_cache_data() returns an empty dictionary. "
            "Consider using Redis backend if you need cache monitoring.",
            RuntimeWarning,
            stacklevel=2,
        )
        logger.debug("Memcached GET_CACHE_DATA unsupported; returning empty dict")
        return {}
