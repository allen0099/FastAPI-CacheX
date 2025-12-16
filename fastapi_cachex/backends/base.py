from abc import ABC
from abc import abstractmethod

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
