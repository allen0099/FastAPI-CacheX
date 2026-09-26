"""In-memory cache backend implementation."""

import asyncio
import fnmatch
import logging
import time
from collections.abc import Callable
from collections.abc import Iterable

from fastapi_cachex.types import CACHE_KEY_SEPARATOR
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import CacheItem
from fastapi_cachex.types import counter_entry
from fastapi_cachex.types import counter_value

from .base import BaseCacheBackend
from .base import validate_delta
from .base import validate_ttl
from .base import warn_if_path_shaped

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


def _cancel(task: "asyncio.Task[None]") -> None:
    """Cancel ``task`` from any thread; a task on a closed loop is left alone."""
    loop = task.get_loop()
    if loop.is_closed():
        # Cancelling would schedule a callback on the closed loop and raise.
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        task.cancel()
    else:
        loop.call_soon_threadsafe(task.cancel)


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

        Raises:
            ValueError: If ``cleanup_interval`` is not positive
        """
        if cleanup_interval <= 0:
            # asyncio.sleep() returns at once for these, so the cleanup loop
            # would spin, taking the cache lock on every pass.
            msg = f"cleanup_interval must be positive, got {cleanup_interval!r}"
            raise ValueError(msg)
        self.cache: dict[str, CacheItem] = {}
        self.lock = asyncio.Lock()
        self.cleanup_interval = cleanup_interval
        self._cleanup_task: asyncio.Task[None] | None = None

    def _ensure_cleanup_started(self) -> None:
        """Ensure a cleanup task runs on the current event loop.

        A task left on another loop, for example one that has since been
        closed, never runs again, so it is replaced rather than reused.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop yet; defer until first real async call.
            return
        task = self._cleanup_task
        if task is not None and not task.done():
            if task.get_loop() is loop:
                return
            _cancel(task)
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
        """Stop the cleanup task if it's running.

        This only requests cancellation. Use ``aclose()`` to also wait until
        the task has finished.
        """
        if self._cleanup_task is not None:
            _cancel(self._cleanup_task)
            self._cleanup_task = None
            logger.debug("Stopped memory backend cleanup task")

    async def aclose(self) -> None:
        """Stop the cleanup task and wait until it has finished.

        Safe to call more than once. A task that belongs to another event loop
        cannot be awaited here; it is only asked to cancel, as ``stop_cleanup()``
        does.
        """
        task = self._cleanup_task
        self.stop_cleanup()
        if task is not None and task.get_loop() is asyncio.get_running_loop():
            # wait() does not raise the task's CancelledError, so a
            # cancellation of aclose() itself still propagates.
            await asyncio.wait([task])

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

        Starting the sweeper here as well as in ``get`` matters for a
        write-mostly user — `StateManager.create_state` only writes, say — who
        would otherwise accumulate expired entries forever, since nothing else
        ever starts it.

        Args:
            key: Cache key
            value: Content to cache
            ttl: Time to live in seconds (None = never expires)
        """
        validate_ttl(ttl)
        self._ensure_cleanup_started()

        async with self.lock:
            expiry = time.time() + ttl if ttl is not None else None
            self.cache[key] = CacheItem(value=value, expiry=expiry)
            logger.debug("Memory cache SET; key=%s ttl=%s", key, ttl)

    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""
        async with self.lock:
            self.cache.pop(key, None)
            logger.debug("Memory cache DELETE; key=%s", key)

    async def delete_many(self, keys: Iterable[str]) -> int:
        """Remove every key in ``keys`` under one lock; returns how many existed."""
        doomed = set(keys)
        removed = await self._evict(doomed.__contains__)
        logger.debug(
            "Memory cache DELETE_MANY; requested=%s removed=%s", len(doomed), removed
        )
        return removed

    async def get_and_delete(self, key: str) -> CacheEntry | None:
        """Atomically retrieve and remove a cached entry (see base class)."""
        self._ensure_cleanup_started()

        async with self.lock:
            item = self.cache.pop(key, None)
            if item is None or not _is_live(item, time.time()):
                logger.debug("Memory cache GET_AND_DELETE MISS; key=%s", key)
                return None
            logger.debug("Memory cache GET_AND_DELETE HIT; key=%s", key)
            return item.value

    async def set_if_absent(
        self, key: str, value: CacheEntry, ttl: int | None = None
    ) -> bool:
        """Atomically store ``value`` unless ``key`` exists (see base class)."""
        validate_ttl(ttl)
        self._ensure_cleanup_started()

        async with self.lock:
            now = time.time()
            item = self.cache.get(key)
            if item is not None and _is_live(item, now):
                logger.debug("Memory cache SET_IF_ABSENT EXISTS; key=%s", key)
                return False
            expiry = now + ttl if ttl is not None else None
            self.cache[key] = CacheItem(value=value, expiry=expiry)
            logger.debug("Memory cache SET_IF_ABSENT STORED; key=%s ttl=%s", key, ttl)
            return True

    async def delete_if_equals(self, key: str, expected: CacheEntry) -> bool:
        """Atomically remove ``key`` while it holds ``expected`` (see base class)."""
        async with self.lock:
            item = self.cache.get(key)
            if item is None or not _is_live(item, time.time()):
                logger.debug("Memory cache DELETE_IF_EQUALS MISS; key=%s", key)
                return False
            if item.value != expected:
                logger.debug("Memory cache DELETE_IF_EQUALS MISMATCH; key=%s", key)
                return False
            del self.cache[key]
            logger.debug("Memory cache DELETE_IF_EQUALS HIT; key=%s", key)
            return True

    async def expire_if_equals(self, key: str, expected: CacheEntry, ttl: int) -> bool:
        """Atomically update expiry on ``key`` while it holds ``expected`` (see base class)."""
        validate_ttl(ttl)
        async with self.lock:
            item = self.cache.get(key)
            if item is None or not _is_live(item, time.time()):
                logger.debug("Memory cache EXPIRE_IF_EQUALS MISS; key=%s", key)
                return False
            if item.value != expected:
                logger.debug("Memory cache EXPIRE_IF_EQUALS MISMATCH; key=%s", key)
                return False
            item.expiry = time.time() + ttl
            logger.debug("Memory cache EXPIRE_IF_EQUALS HIT; key=%s ttl=%s", key, ttl)
            return True

    async def increment(self, key: str, delta: int = 1, ttl: int | None = None) -> int:
        """Atomically add ``delta`` to the counter at ``key`` (see base class).

        The read-modify-write happens under the backend lock, so concurrent
        callers on the same event loop never lose an increment.
        """
        validate_delta(delta)
        validate_ttl(ttl)
        self._ensure_cleanup_started()

        async with self.lock:
            now = time.time()
            item = self.cache.get(key)
            if item is not None and _is_live(item, now):
                value = counter_value(item.value) + delta
                item.value = counter_entry(value)
            else:
                value = delta
                expiry = now + ttl if ttl is not None else None
                self.cache[key] = CacheItem(value=counter_entry(value), expiry=expiry)
            logger.debug("Memory cache INCREMENT; key=%s value=%s", key, value)
            return value

    async def clear(self) -> None:
        """Clear all cached responses."""
        async with self.lock:
            self.cache.clear()
            logger.debug("Memory cache CLEAR; all entries removed")

    async def _evict(self, matches: Callable[[str], bool]) -> int:
        """Remove every entry whose key satisfies ``matches``.

        Returns how many of them had not expired yet: an expired entry is
        already gone as far as callers can tell, as it is on Redis.
        """
        async with self.lock:
            now = time.time()
            doomed = [key for key in self.cache if matches(key)]
            return sum(_is_live(self.cache.pop(key), now) for key in doomed)

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
        """Clear cached entries whose key matches a glob pattern (see base class).

        Matches ``fnmatch`` against the whole key, which is what the Redis
        backend's SCAN does. Matching only the path component, as this used to,
        made the same call clear different things on different backends.
        Matching is case-sensitive on every platform, as on Redis; plain
        ``fnmatch.fnmatch`` folds case on Windows.

        Args:
            pattern: A glob pattern to match whole cache keys against

        Returns:
            Number of cache entries cleared
        """
        cleared_count = await self._evict(lambda key: fnmatch.fnmatchcase(key, pattern))
        warn_if_path_shaped(pattern, cleared_count)
        logger.debug(
            "Memory cache CLEAR_PATTERN; pattern=%s removed=%s", pattern, cleared_count
        )
        return cleared_count

    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Returns:
            List of every cache key that has not expired
        """
        async with self.lock:
            now = time.time()
            return [key for key, item in self.cache.items() if _is_live(item, now)]

    async def get_cache_data(self) -> dict[str, tuple[CacheEntry, float | None]]:
        """Get all cache data with expiry information.

        Returns:
            Dictionary mapping cache keys to (CacheEntry, expiry) tuples, for
            entries that have not expired
        """
        async with self.lock:
            now = time.time()
            return {
                key: (item.value, item.expiry)
                for key, item in self.cache.items()
                if _is_live(item, now)
            }

    async def _cleanup_task_impl(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.cleanup_interval)
                await self.cleanup()
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
