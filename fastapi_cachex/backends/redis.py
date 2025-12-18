"""Redis cache backend implementation."""

from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import ETagContent

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

try:
    import orjson as json

except ImportError:  # pragma: no cover
    import json  # type: ignore[no-redef]  # pragma: no cover

# Default Redis key prefix for fastapi-cachex
DEFAULT_REDIS_PREFIX = "fastapi_cachex:"


class AsyncRedisCacheBackend(BaseCacheBackend):
    """Async Redis cache backend implementation.

    This backend uses Redis with a key prefix to avoid conflicts with other
    applications. Keys are namespaced with 'fastapi_cachex:' by default.
    """

    client: "AsyncRedis[str]"
    key_prefix: str

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: str | None = None,
        db: int = 0,
        encoding: str = "utf-8",
        decode_responses: Literal[True] = True,
        socket_timeout: float = 1.0,
        socket_connect_timeout: float = 1.0,
        key_prefix: str = DEFAULT_REDIS_PREFIX,
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
            key_prefix: Prefix for all cache keys (default: 'fastapi_cachex:')
            **kwargs: Additional arguments to pass to Redis client
        """
        try:
            from redis.asyncio import Redis as AsyncRedis
        except ImportError:
            msg = "redis[hiredis] is not installed. Please install it with 'pip install \"redis[hiredis]\"' "
            raise CacheXError(msg)

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
        self.key_prefix = key_prefix

    def _make_key(self, key: str) -> str:
        """Add prefix to cache key."""
        return f"{self.key_prefix}{key}"

    def _serialize(self, value: ETagContent) -> str:
        """Serialize ETagContent to JSON string."""
        if isinstance(value.content, bytes):
            content = value.content.decode()
        else:
            content = value.content

        serialized: str | bytes = json.dumps(
            {
                "etag": value.etag,
                "content": content,
            },
        )

        # orjson returns bytes, stdlib json returns str
        return serialized.decode() if isinstance(serialized, bytes) else serialized

    def _deserialize(self, value: str | None) -> ETagContent | None:
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

    async def get(self, key: str) -> ETagContent | None:
        """Retrieve a cached response."""
        result = await self.client.get(self._make_key(key))
        return self._deserialize(result)

    async def set(self, key: str, value: ETagContent, ttl: int | None = None) -> None:
        """Store a response in the cache."""
        serialized = self._serialize(value)
        prefixed_key = self._make_key(key)
        if ttl is not None:
            await self.client.setex(prefixed_key, ttl, serialized)
        else:
            await self.client.set(prefixed_key, serialized)

    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""
        await self.client.delete(self._make_key(key))

    async def clear(self) -> None:
        """Clear all cached responses for this namespace.

        Uses SCAN instead of KEYS to avoid blocking in production.
        Only deletes keys within this backend's prefix.
        """
        pattern = f"{self.key_prefix}*"
        cursor = 0
        batch_size = 100
        keys_to_delete: list[str] = []

        # Use SCAN to iterate through keys without blocking
        while True:
            cursor, keys = await self.client.scan(
                cursor,
                match=pattern,
                count=batch_size,
            )
            if keys:
                keys_to_delete.extend(keys)
            if cursor == 0:
                break

        # Delete all collected keys in batches to avoid huge command size
        if keys_to_delete:
            for i in range(0, len(keys_to_delete), batch_size):
                batch = keys_to_delete[i : i + batch_size]
                if batch:
                    await self.client.delete(*batch)

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path.

        Uses SCAN instead of KEYS to avoid blocking in production.

        Args:
            path: The path to clear cache for
            include_params: Whether to clear all parameter variations

        Returns:
            Number of cache entries cleared
        """
        # Pattern includes the HTTP method, host, and path components
        if include_params:
            # Clear all variations: *:path:*
            pattern = f"{self.key_prefix}*:{path}:*"
        else:
            # Clear only exact path (no query params): *:path
            pattern = f"{self.key_prefix}*:{path}"

        cursor = 0
        batch_size = 100
        cleared_count = 0
        keys_to_delete: list[str] = []

        # Use SCAN to iterate through keys without blocking
        while True:
            cursor, keys = await self.client.scan(
                cursor,
                match=pattern,
                count=batch_size,
            )
            if keys:
                keys_to_delete.extend(keys)
            if cursor == 0:
                break

        # Delete all collected keys in batches
        if keys_to_delete:
            for i in range(0, len(keys_to_delete), batch_size):
                batch = keys_to_delete[i : i + batch_size]
                if batch:
                    deleted = await self.client.delete(*batch)
                    cleared_count += deleted

        return cleared_count

    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern.

        Uses SCAN instead of KEYS to avoid blocking in production.

        Args:
            pattern: A glob pattern to match cache keys against

        Returns:
            Number of cache entries cleared
        """
        # Ensure pattern includes the key prefix
        if not pattern.startswith(self.key_prefix):
            full_pattern = f"{self.key_prefix}{pattern}"
        else:
            full_pattern = pattern

        cursor = 0
        batch_size = 100
        cleared_count = 0
        keys_to_delete: list[str] = []

        # Use SCAN to iterate through keys without blocking
        while True:
            cursor, keys = await self.client.scan(
                cursor,
                match=full_pattern,
                count=batch_size,
            )
            if keys:
                keys_to_delete.extend(keys)
            if cursor == 0:
                break

        # Delete all collected keys in batches
        if keys_to_delete:
            for i in range(0, len(keys_to_delete), batch_size):
                batch = keys_to_delete[i : i + batch_size]
                if batch:
                    deleted = await self.client.delete(*batch)
                    cleared_count += deleted

        return cleared_count

    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Returns:
            List of all cache keys currently stored in the backend
        """
        pattern = f"{self.key_prefix}*"
        cursor = 0
        batch_size = 100
        all_keys: list[str] = []

        # Use SCAN to iterate through keys without blocking
        while True:
            cursor, keys = await self.client.scan(
                cursor,
                match=pattern,
                count=batch_size,
            )
            if keys:
                all_keys.extend(keys)
            if cursor == 0:
                break

        return all_keys

    async def get_cache_data(self) -> dict[str, tuple[ETagContent, float | None]]:
        """Get all cache data with expiry information.

        Returns:
            Dictionary mapping cache keys to (ETagContent, expiry) tuples.
            Note: Redis stores TTL but not absolute expiry time, so this
            returns None for expiry (no expiry tracking in Redis backend).
        """
        all_keys = await self.get_all_keys()
        cache_data: dict[str, tuple[ETagContent, float | None]] = {}

        for key in all_keys:
            value = await self.get(key)
            if value is not None:
                # Remove key prefix to get the original cache key
                original_key = key.removeprefix(self.key_prefix)
                cache_data[original_key] = (value, None)

        return cache_data
