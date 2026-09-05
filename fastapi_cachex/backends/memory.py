"""In-memory cache backend implementation."""

import asyncio
import fnmatch
import logging
import time
from collections.abc import Callable

from fastapi_cachex.types import CACHE_KEY_SEPARATOR
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import CacheItem

from .base import BaseCacheBackend

logger = logging.getLogger(__name__)

# HTTP cache keys are formatted as: method|||host|||path|||query_params
_PATH_INDEX = 2
_QUERY_INDEX = 3


def _split_http_key(key: str) -> tuple[str, bool] | None:
    """Return ``(path, has_query_params)`` for an HTTP cache key, else ``None``.

    Keys without separators (CacheManager/StateManager keys or custom key
    builders) are not HTTP keys and are matched on their raw value instead.
    """
    parts = key.split(CACHE_KEY_SEPARATOR, _QUERY_INDEX)
    if len(parts) <= _PATH_INDEX:
        return None
    has_params = len(parts) > _QUERY_INDEX and bool(parts[_QUERY_INDEX])
    return parts[_PATH_INDEX], has_params


def _is_live(item: CacheItem, now: float) -> bool:
    """Whether ``item`` has not expired at ``now``."""
    return item.expiry is None or item.expiry > now


class MemoryBackend(BaseCacheBackend):
    """In-memory cache backend implementation.

    Manages an in-memory cache dictionary with automatic expiration cleanup.
    Cleanup runs in a background task that periodically removes expired entries.
    Cleanup is lazily initialized on first cache operation to ensure proper
    async context.
    """

    def __init__(self, cleanup_interval: int = 60) -> None:
        """Initialize in-memory cache backend.

        Args:
            cleanup_interval: Interval in seconds between cleanup runs (default: 60)
        """
        self.cache: dict[str, CacheItem] = {}
        self.lock = asyncio.Lock()
        self.cleanup_interval = cleanup_interval
        self._cleanup_task: asyncio.Task[None] | None = None

    def _ensure_cleanup_started(self) -> None:
        """Ensure cleanup task is started in proper async context."""
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running event loop yet; defer until first real async call.
                return
            self._cleanup_task = loop.create_task(self._cleanup_task_impl())
            logger.debug(
                "Started memory backend cleanup task (interval=%s)",
                self.cleanup_interval,
            )

    def start_cleanup(self) -> None:
        """Start the cleanup task if it's not already running.

        Cleanup is lazily started to ensure it's created in proper async context.
        """
        self._ensure_cleanup_started()

    def stop_cleanup(self) -> None:
        """Stop the cleanup task if it's running."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
            logger.debug("Stopped memory backend cleanup task")

    async def get(self, key: str) -> CacheEntry | None:
        """Retrieve a cached response.

        Expired entries are skipped and return None.
        Ensures cleanup task is started.
        """
        self._ensure_cleanup_started()

        async with self.lock:
            cached_item = self.cache.get(key)
            if cached_item is None:
                logger.debug("Memory cache MISS; key=%s", key)
                return None
            if not _is_live(cached_item, time.time()):
                # Entry has expired; clean it up
                del self.cache[key]
                logger.debug("Memory cache EXPIRED; key=%s removed", key)
                return None
            logger.debug("Memory cache HIT; key=%s", key)
            return cached_item.value

    async def set(self, key: str, value: CacheEntry, ttl: int | None = None) -> None:
        """Store a response in the cache.

        Args:
            key: Cache key
            value: Content to cache
            ttl: Time to live in seconds (None = never expires)
        """
        async with self.lock:
            expiry = time.time() + ttl if ttl is not None else None
            self.cache[key] = CacheItem(value=value, expiry=expiry)
            logger.debug("Memory cache SET; key=%s ttl=%s", key, ttl)

    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""
        async with self.lock:
            self.cache.pop(key, None)
            logger.debug("Memory cache DELETE; key=%s", key)

    async def clear(self) -> None:
        """Clear all cached responses."""
        async with self.lock:
            self.cache.clear()
            logger.debug("Memory cache CLEAR; all entries removed")

    async def _evict(self, matches: Callable[[str], bool]) -> int:
        """Remove every entry whose key satisfies ``matches``; returns the count."""
        async with self.lock:
            doomed = [key for key in self.cache if matches(key)]
            for key in doomed:
                del self.cache[key]
        return len(doomed)

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path.

        Parses cache keys to extract the path component and matches against
        the provided path.

        Args:
            path: The path to clear cache for
            include_params: If True, clear all variations including query params
                           If False, only clear exact path (no query params)

        Returns:
            Number of cache entries cleared
        """

        def matches(key: str) -> bool:
            parsed = _split_http_key(key)
            if parsed is None:
                # Direct key match (custom key format without separators)
                return key == path
            cache_path, has_params = parsed
            return cache_path == path and (include_params or not has_params)

        cleared_count = await self._evict(matches)
        logger.debug(
            "Memory cache CLEAR_PATH; path=%s include_params=%s removed=%s",
            path,
            include_params,
            cleared_count,
        )
        return cleared_count

    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern.

        Uses fnmatch for glob-style pattern matching against the path component
        of cache keys.

        Args:
            pattern: A glob pattern to match against paths (e.g., "/users/*")

        Returns:
            Number of cache entries cleared
        """

        def matches(key: str) -> bool:
            parsed = _split_http_key(key)
            subject = key if parsed is None else parsed[0]
            return fnmatch.fnmatch(subject, pattern)

        cleared_count = await self._evict(matches)
        logger.debug(
            "Memory cache CLEAR_PATTERN; pattern=%s removed=%s", pattern, cleared_count
        )
        return cleared_count

    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Returns:
            List of all cache keys currently stored in the backend
        """
        async with self.lock:
            return list(self.cache.keys())

    async def get_cache_data(self) -> dict[str, tuple[CacheEntry, float | None]]:
        """Get all cache data with expiry information.

        Returns:
            Dictionary mapping cache keys to (CacheEntry, expiry) tuples
        """
        async with self.lock:
            return {key: (item.value, item.expiry) for key, item in self.cache.items()}

    async def _cleanup_task_impl(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.cleanup_interval)
                await self.cleanup()  # pragma: no cover
        except asyncio.CancelledError:
            # Handle task cancellation gracefully
            pass

    async def cleanup(self) -> None:
        """Remove expired cache entries from memory."""
        async with self.lock:
            now = time.time()
            expired_keys = [k for k, v in self.cache.items() if not _is_live(v, now)]
            for key in expired_keys:
                del self.cache[key]
            if expired_keys:
                logger.debug(
                    "Memory cache CLEANUP; expired removed=%s", len(expired_keys)
                )
