"""Base cache backend interface and abstract implementation."""

from abc import ABC
from abc import abstractmethod
from typing import Any

from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry
from fastapi_cachex.types import counter_value


class BaseCacheBackend(ABC):
    """Base class for all cache backends."""

    @abstractmethod
    async def get(self, key: str) -> CacheEntry | None:
        """Retrieve a cached response."""

    @abstractmethod
    async def set(self, key: str, value: CacheEntry, ttl: int | None = None) -> None:
        """Store a response in the cache."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""

    async def get_and_delete(self, key: str) -> CacheEntry | None:
        """Atomically retrieve and remove a cached entry.

        Use this for one-shot values (OAuth states, grants, invalidation) where
        exactly one of several concurrent callers may win: every other caller
        sees ``None``.

        The base implementation is a best-effort, NON-atomic get-then-delete
        fallback for third-party backends; the built-in backends override it
        with an atomic implementation.

        Returns:
            The entry that was stored under ``key``, or ``None`` if there was none
        """
        value = await self.get(key)
        if value is not None:
            await self.delete(key)
        return value

    async def increment(self, key: str, delta: int = 1, ttl: int | None = None) -> int:
        """Atomically add ``delta`` to the integer counter stored at ``key``.

        A missing key counts as 0: the first call creates the counter with the
        value ``delta`` and applies ``ttl`` (seconds; ``None`` = never expires).
        Later calls keep the existing expiry, so the counter lives in a fixed
        window that starts when it is created - the shape rate limiters need.
        The counter is readable through ``get()`` as a ``CacheEntry`` whose
        fingerprint is ``COUNTER_FINGERPRINT`` and whose content is the decimal
        value; ``delete``/``clear*`` treat it like any other entry.

        The base implementation is a best-effort, NON-atomic read-modify-write
        fallback for third-party backends and re-applies ``ttl`` on every call.
        The built-in backends override it with a single server-side operation.

        Args:
            key: Cache key of the counter
            delta: Amount to add (may be negative)
            ttl: Time to live in seconds, applied when the counter is created

        Returns:
            The counter value after the increment

        Raises:
            CacheXError: If ``key`` holds a cached response instead of a counter
        """
        current = await self.get(key)
        value = delta if current is None else counter_value(current) + delta
        await self.set(key, counter_entry(value), ttl=ttl)
        return value

    @abstractmethod
    async def clear(self) -> None:
        """Clear all cached responses."""

    @abstractmethod
    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path.

        Args:
            path: The path to clear cache for
            include_params: Whether to clear all parameter variations of the path

        Returns:
            Number of cache entries cleared
        """

    @abstractmethod
    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern.

        Args:
            pattern: A glob pattern to match cache keys against (e.g., "/users/*")

        Returns:
            Number of cache entries cleared
        """

    @abstractmethod
    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Returns:
            List of all cache keys currently stored in the backend
        """

    @abstractmethod
    async def get_cache_data(self) -> dict[str, tuple[Any, float | None]]:
        """Get all cache data with expiry information.

        This method is primarily used for cache monitoring and statistics.
        Returns cache keys mapped to tuples of (value, expiry_time).

        Returns:
            Dictionary mapping cache keys to (value, expiry) tuples.
            Expiry is None if the item never expires.
        """
