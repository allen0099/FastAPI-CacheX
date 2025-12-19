"""In-memory cache backend implementation."""

import asyncio
import contextlib
import fnmatch
import logging
import time

from fastapi_cachex.types import CACHE_KEY_SEPARATOR
from fastapi_cachex.types import CacheItem
from fastapi_cachex.types import ETagContent

from .base import BaseCacheBackend

# Cache keys are formatted as: method|||host|||path|||query_params
# Minimum parts required to extract path component
_MIN_KEY_PARTS = 3
# Maximum parts to split (method, host, path, query_params)
_MAX_KEY_PARTS = 3

# Module-level logger (inherits package logger)
logger = logging.getLogger(__name__)


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
            with contextlib.suppress(RuntimeError):
                # No event loop yet; will be created on first async operation
                self._cleanup_task = asyncio.create_task(self._cleanup_task_impl())
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

    async def get(self, key: str) -> ETagContent | None:
        """Retrieve a cached response.

        Expired entries are skipped and return None.
        Ensures cleanup task is started.
        """
        self._ensure_cleanup_started()

        async with self.lock:
            cached_item = self.cache.get(key)
            if cached_item:
                if cached_item.expiry is None or cached_item.expiry > time.time():
                    logger.debug("Memory cache HIT; key=%s", key)
                    return cached_item.value
                # Entry has expired; clean it up
                del self.cache[key]
                logger.debug("Memory cache EXPIRED; key=%s removed", key)
                return None
            logger.debug("Memory cache MISS; key=%s", key)
            return None

    async def set(self, key: str, value: ETagContent, ttl: int | None = None) -> None:
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
        cleared_count = 0
        async with self.lock:
            keys_to_delete = []
            for key in self.cache:
                # Keys are formatted as: method|||host|||path|||query_params
                parts = key.split(CACHE_KEY_SEPARATOR, _MAX_KEY_PARTS)
                if len(parts) >= _MIN_KEY_PARTS:
                    cache_path = parts[2]
                    has_params = len(parts) > _MIN_KEY_PARTS
                    if cache_path == path and (include_params or not has_params):
                        keys_to_delete.append(key)
                        cleared_count += 1

            for key in keys_to_delete:
                del self.cache[key]

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
        cleared_count = 0
        async with self.lock:
            keys_to_delete = []
            for key in self.cache:
                # Extract path component (method|||host|||path|||query_params)
                parts = key.split(CACHE_KEY_SEPARATOR, _MAX_KEY_PARTS)
                if len(parts) >= _MIN_KEY_PARTS:
                    cache_path = parts[2]
                    if fnmatch.fnmatch(cache_path, pattern):
                        keys_to_delete.append(key)
                        cleared_count += 1

            for key in keys_to_delete:
                del self.cache[key]

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

    async def get_cache_data(self) -> dict[str, tuple[ETagContent, float | None]]:
        """Get all cache data with expiry information.

        Returns:
            Dictionary mapping cache keys to (ETagContent, expiry) tuples
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
            expired_keys = [
                k
                for k, v in self.cache.items()
                if v.expiry is not None and v.expiry <= now
            ]
            for key in expired_keys:
                self.cache.pop(key, None)
            if expired_keys:
                logger.debug(
                    "Memory cache CLEANUP; expired removed=%s", len(expired_keys)
                )
