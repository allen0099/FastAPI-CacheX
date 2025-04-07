from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import Optional

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import ETagContent

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

try:
    import orjson as json

except ImportError:  # pragma: no cover
    import json  # type: ignore[no-redef]


class AsyncRedisCacheBackend(BaseCacheBackend):
    """Async Redis cache backend implementation."""

    client: "AsyncRedis[str]"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: Optional[str] = None,
        db: int = 0,
        encoding: str = "utf-8",
        decode_responses: Literal[True] = True,
        socket_timeout: float = 1.0,
        socket_connect_timeout: float = 1.0,
        **kwargs: Any,
    ) -> None:
        """Initialize async Redis cache backend.

        Args:
            host: Redis host
            port: Redis port
            password: Redis password
            db: Redis database number
            encoding: Character encoding to use
            decode_responses: Whether to decode response automatically
            socket_timeout: Timeout for socket operations (in seconds)
            socket_connect_timeout: Timeout for socket connection (in seconds)
            **kwargs: Additional arguments to pass to Redis client
        """
        try:
            from redis.asyncio import Redis as AsyncRedis
        except ImportError:
            raise CacheXError(
                "redis[hiredis] is not installed. Please install it with 'pip install \"redis[hiredis]\"'"
            )

        self.client = AsyncRedis(
            host=host,
            port=port,
            password=password,
            db=db,
            encoding=encoding,
            decode_responses=decode_responses,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            **kwargs,
        )

    def _serialize(self, value: ETagContent) -> str:
        """Serialize ETagContent to JSON string."""
        serialized = json.dumps(
            {
                "etag": value.etag,
                "content": value.content.decode()
                if isinstance(value.content, bytes)
                else value.content,
            }
        )

        if isinstance(serialized, bytes):
            # If using orjson, it returns bytes
            return serialized.decode()

        return serialized  # type: ignore[unreachable]

    def _deserialize(self, value: Optional[str]) -> Optional[ETagContent]:
        """Deserialize JSON string to ETagContent."""
        if value is None:
            return None
        try:
            data = json.loads(value)
            return ETagContent(
                etag=data["etag"],
                content=data["content"].encode()
                if isinstance(data["content"], str)
                else data["content"],
            )
        except (json.JSONDecodeError, KeyError):
            return None

    async def get(self, key: str) -> Optional[ETagContent]:
        """Retrieve a cached response."""
        result = await self.client.get(key)
        return self._deserialize(result)

    async def set(
        self, key: str, value: ETagContent, ttl: Optional[int] = None
    ) -> None:
        """Store a response in the cache."""
        serialized = self._serialize(value)
        if ttl is not None:
            await self.client.setex(key, ttl, serialized)
        else:
            await self.client.set(key, serialized)

    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""
        await self.client.delete(key)

    async def clear(self) -> None:
        """Clear all cached responses."""
        await self.client.flushdb()

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path."""
        pattern = f"{path}*" if include_params else path
        keys = await self.client.keys(pattern)
        if keys:
            return await self.client.delete(*keys)
        return 0

    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern."""
        keys = await self.client.keys(pattern)
        if keys:
            return await self.client.delete(*keys)
        return 0
