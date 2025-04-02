from typing import Optional

from fastapi import Response

from .base import BaseCacheBackend


class InMemoryCache(BaseCacheBackend):
    """In-memory cache backend implementation."""

    def __init__(self) -> None:
        self.cache: dict[str, Response] = {}

    async def get(self, key: str) -> Optional[Response]:
        return self.cache.get(key)

    async def set(self, key: str, response: Response) -> None:
        self.cache[key] = response

    async def delete(self, key: str) -> None:
        self.cache.pop(key, None)

    async def clear(self) -> None:
        self.cache.clear()
