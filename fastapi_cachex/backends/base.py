from abc import ABC
from abc import abstractmethod
from typing import Optional

from fastapi import Response


class BaseCacheBackend(ABC):
    """Base class for all cache backends."""

    @abstractmethod
    async def get(self, key: str) -> Optional[Response]:
        """Retrieve a cached response."""

    @abstractmethod
    async def set(self, key: str, response: Response) -> None:
        """Store a response in the cache."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""

    @abstractmethod
    async def clear(self) -> None:
        """Clear all cached responses."""
