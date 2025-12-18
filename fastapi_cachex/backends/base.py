"""Base cache backend interface and abstract implementation."""

from abc import ABC
from abc import abstractmethod
from typing import Any

from fastapi_cachex.types import ETagContent


class BaseCacheBackend(ABC):
    """Base class for all cache backends."""

    @abstractmethod
    async def get(self, key: str) -> ETagContent | None:
        """Retrieve a cached response."""

    @abstractmethod
    async def set(self, key: str, value: ETagContent, ttl: int | None = None) -> None:
        """Store a response in the cache."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""

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
