"""In-memory cache backend implementation."""

import asyncio
import copy
import fnmatch
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from collections.abc import Iterable
from datetime import timedelta

from fastapi_cachex.cache_key import CacheKey
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import CacheItem
from fastapi_cachex.types import counter_entry
from fastapi_cachex.types import counter_value

from .base import BaseCacheBackend
from .base import check_counter_range
from .base import validate_delta
from .base import validate_ttl
from .base import warn_if_path_shaped

logger = logging.getLogger(__name__)


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


def _validate_max_entries(max_entries: int | None) -> int | None:
    """Return ``max_entries`` if it is ``None`` or a positive ``int``.

    Raises:
        TypeError: If ``max_entries`` is not an ``int`` (``bool`` included)
        ValueError: If ``max_entries`` is zero or negative
    """
    if max_entries is None:
        return None
    if isinstance(max_entries, bool) or not isinstance(max_entries, int):
        msg = f"max_entries must be an int or None, got {type(max_entries).__name__}"
        raise TypeError(msg)
    if max_entries <= 0:
        msg = f"max_entries must be positive, got {max_entries!r}"
        raise ValueError(msg)
    return max_entries


class MemoryBackend(BaseCacheBackend):
    """In-memory cache backend implementation.

    Manages an in-memory cache dictionary with automatic expiration cleanup.
    Cleanup runs in a background task that periodically removes expired entries.
    Cleanup is lazily initialized on first cache operation to ensure proper
    async context.

    Without ``max_entries`` the cache grows until entries expire, by request
    rate times TTL times body size, and a client choosing query strings can
    grow it on purpose. With ``max_entries`` the cache holds at most that
    many entries: storing one more evicts the least recently used entry,
    expired or not. A read hit or any write counts as a use.
    """

    def __init__(
        self, cleanup_interval: int = 60, *, max_entries: int | None = None
    ) -> None:
        """Initialize in-memory cache backend.

        Args:
            cleanup_interval: Interval in seconds between cleanup runs (default: 60)
            max_entries: How many entries the cache holds at most (default:
                no limit). When full, the least recently used entry is
                evicted to make room.

        Raises:
            TypeError: If ``max_entries`` is not an ``int`` or ``None``
            ValueError: If ``cleanup_interval`` or ``max_entries`` is not
                positive
        """
        if cleanup_interval <= 0:
            # asyncio.sleep() returns at once for these, so the cleanup loop
            # would spin, taking the cache lock on every pass.
            msg = f"cleanup_interval must be positive, got {cleanup_interval!r}"
            raise ValueError(msg)
        # Ordered from least to most recently used; a plain `dict` for callers
        # (`OrderedDict` is one), with `move_to_end` for the LRU bookkeeping.
        self.cache: OrderedDict[str, CacheItem] = OrderedDict()
        self.lock = asyncio.Lock()
        self.cleanup_interval = cleanup_interval
        self.max_entries = _validate_max_entries(max_entries)
        self._cleanup_task: asyncio.Task[None] | None = None

    def _touch(self, key: str) -> None:
        """Mark ``key`` as the most recently used entry. Call under the lock."""
        self.cache.move_to_end(key)

    def _store(self, key: str, item: CacheItem) -> None:
        """Put ``item`` under ``key`` as the most recently used entry.

        Evicts the least recently used entry when that leaves the cache over
        ``max_entries``. Call under the lock.
        """
        self.cache[key] = item
        self.cache.move_to_end(key)
        if self.max_entries is not None and len(self.cache) > self.max_entries:
            evicted, _ = self.cache.popitem(last=False)
            logger.debug(
                "Memory cache EVICT; key=%s max_entries=%s", evicted, self.max_entries
            )

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
            self._touch(key)
            logger.debug("Memory cache HIT; key=%s", key)
            return copy.copy(cached_item.value)

    async def set(
        self, key: str, value: CacheEntry, ttl: int | timedelta | None = None
    ) -> None:
        """Store a response in the cache.

        Starting the sweeper here as well as in ``get`` matters for a
        write-mostly user, who would otherwise accumulate expired entries
        forever, since nothing else ever starts it.

        Args:
            key: Cache key
            value: Content to cache
            ttl: Time to live in seconds (None = never expires)
        """
        ttl = validate_ttl(ttl)
        self._ensure_cleanup_started()

        async with self.lock:
            expiry = time.time() + ttl if ttl is not None else None
            self._store(key, CacheItem(value=copy.copy(value), expiry=expiry))
            logger.debug("Memory cache SET; key=%s ttl=%s", key, ttl)

    async def delete(self, key: str) -> bool:
        """Remove a response from the cache; returns whether it had not expired."""
        async with self.lock:
            item = self.cache.pop(key, None)
            removed = item is not None and _is_live(item, time.time())
            logger.debug("Memory cache DELETE; key=%s removed=%s", key, removed)
            return removed

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
        self, key: str, value: CacheEntry, ttl: int | timedelta | None = None
    ) -> bool:
        """Atomically store ``value`` unless ``key`` exists (see base class)."""
        ttl = validate_ttl(ttl)
        self._ensure_cleanup_started()

        async with self.lock:
            now = time.time()
            item = self.cache.get(key)
            if item is not None and _is_live(item, now):
                logger.debug("Memory cache SET_IF_ABSENT EXISTS; key=%s", key)
                return False
            expiry = now + ttl if ttl is not None else None
            self._store(key, CacheItem(value=copy.copy(value), expiry=expiry))
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

    async def expire_if_equals(
        self, key: str, expected: CacheEntry, ttl: int | timedelta
    ) -> bool:
        """Atomically update expiry on ``key`` while it holds ``expected`` (see base class)."""
        ttl = validate_ttl(ttl)
        async with self.lock:
            item = self.cache.get(key)
            if item is None or not _is_live(item, time.time()):
                logger.debug("Memory cache EXPIRE_IF_EQUALS MISS; key=%s", key)
                return False
            if item.value != expected:
                logger.debug("Memory cache EXPIRE_IF_EQUALS MISMATCH; key=%s", key)
                return False
            item.expiry = time.time() + ttl
            self._touch(key)
            logger.debug("Memory cache EXPIRE_IF_EQUALS HIT; key=%s ttl=%s", key, ttl)
            return True

    async def set_if_equals(
        self,
        key: str,
        expected: CacheEntry,
        value: CacheEntry,
        ttl: int | timedelta | None = None,
    ) -> bool:
        """Atomically store ``value`` while ``key`` holds ``expected`` (see base class)."""
        ttl = validate_ttl(ttl)
        async with self.lock:
            now = time.time()
            item = self.cache.get(key)
            if item is None or not _is_live(item, now):
                logger.debug("Memory cache SET_IF_EQUALS MISS; key=%s", key)
                return False
            if item.value != expected:
                logger.debug("Memory cache SET_IF_EQUALS MISMATCH; key=%s", key)
                return False
            expiry = now + ttl if ttl is not None else None
            self._store(key, CacheItem(value=copy.copy(value), expiry=expiry))
            logger.debug("Memory cache SET_IF_EQUALS HIT; key=%s ttl=%s", key, ttl)
            return True

    async def increment(
        self, key: str, delta: int = 1, ttl: int | timedelta | None = None
    ) -> int:
        """Atomically add ``delta`` to the counter at ``key`` (see base class).

        The read-modify-write happens under the backend lock, so concurrent
        callers on the same event loop never lose an increment. Counters are
        signed 64-bit, as on Redis.

        Raises:
            CacheXError: If the key holds a value that is not a counter, or
                the result would leave the signed 64-bit range (the counter
                is left unchanged).
        """
        validate_delta(delta)
        ttl = validate_ttl(ttl)
        self._ensure_cleanup_started()

        async with self.lock:
            now = time.time()
            item = self.cache.get(key)
            if item is not None and _is_live(item, now):
                value = check_counter_range(counter_value(item.value) + delta)
                item.value = counter_entry(value)
                self._touch(key)
            else:
                value = delta
                expiry = now + ttl if ttl is not None else None
                self._store(key, CacheItem(value=counter_entry(value), expiry=expiry))
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
            parsed = CacheKey.parse(key)
            if parsed is None:
                # Direct key match (custom key format without separators)
                return key == path
            return parsed.path == path and (include_params or not parsed.query)

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
                key: (copy.copy(item.value), item.expiry)
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
