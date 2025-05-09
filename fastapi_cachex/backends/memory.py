import asyncio
import time
from typing import Optional

from fastapi_cachex.types import CacheItem
from fastapi_cachex.types import ETagContent

from .base import BaseCacheBackend


class MemoryBackend(BaseCacheBackend):
    """In-memory cache backend implementation."""

    def __init__(self) -> None:
        self.cache: dict[str, CacheItem] = {}
        self.lock = asyncio.Lock()
        self.cleanup_interval = 60
        self._cleanup_task: Optional[asyncio.Task[None]] = None

    def start_cleanup(self) -> None:
        """Start the cleanup task if it's not already running."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_task_impl())

    def stop_cleanup(self) -> None:
        """Stop the cleanup task if it's running."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    async def get(self, key: str) -> Optional[ETagContent]:
        async with self.lock:
            cached_item = self.cache.get(key)
            if cached_item:
                if cached_item.expiry is None or cached_item.expiry > time.time():
                    return cached_item.value
                else:
                    return None
            return None

    async def set(
        self, key: str, value: ETagContent, ttl: Optional[int] = None
    ) -> None:
        async with self.lock:
            expiry: Optional[int] = int(time.time() + ttl) if ttl is not None else None
            self.cache[key] = CacheItem(value=value, expiry=expiry)

    async def delete(self, key: str) -> None:
        async with self.lock:
            self.cache.pop(key, None)

    async def clear(self) -> None:
        async with self.lock:
            self.cache.clear()

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path."""
        cleared_count = 0
        async with self.lock:
            keys_to_delete = []
            for key in self.cache:
                cache_path, *params = key.split(":", 1)
                if cache_path == path and (include_params or not params):
                    keys_to_delete.append(key)
                    cleared_count += 1

            for key in keys_to_delete:
                del self.cache[key]

        return cleared_count

    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern."""
        import fnmatch

        cleared_count = 0
        async with self.lock:
            keys_to_delete = []
            for key in self.cache:
                cache_path = key.split(":", 1)[0]  # Get path part only
                if fnmatch.fnmatch(cache_path, pattern):
                    keys_to_delete.append(key)
                    cleared_count += 1

            for key in keys_to_delete:
                del self.cache[key]

        return cleared_count

    async def _cleanup_task_impl(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.cleanup_interval)
                await self.cleanup()  # pragma: no cover
        except asyncio.CancelledError:
            # Handle task cancellation gracefully
            pass

    async def cleanup(self) -> None:
        async with self.lock:
            now = time.time()
            expired_keys = [
                k
                for k, v in self.cache.items()
                if v.expiry is not None and v.expiry <= now
            ]
            for key in expired_keys:
                self.cache.pop(key, None)
