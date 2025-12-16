import warnings

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import ETagContent

try:
    import orjson as json

except ImportError:  # pragma: no cover
    import json  # type: ignore[no-redef]  # pragma: no cover

# Default Memcached key prefix for fastapi-cachex
DEFAULT_MEMCACHE_PREFIX = "fastapi_cachex:"


class MemcachedBackend(BaseCacheBackend):
    """Memcached backend implementation.

    Note: This implementation uses synchronous pymemcache client but wraps it
    in async methods. For blocking concerns, consider using aiomcache for
    true async Memcached operations. Keys are namespaced with 'fastapi_cachex:'
    by default to avoid conflicts with other applications.

    Limitations:
    - Pattern-based clearing (clear_pattern) is not supported by Memcached protocol
    - Operations are wrapped to appear async but use blocking sync client internally
    """

    key_prefix: str

    def __init__(
        self,
        servers: list[str],
        key_prefix: str = DEFAULT_MEMCACHE_PREFIX,
    ) -> None:
        """Initialize the Memcached backend.

        Args:
            servers: List of Memcached servers in format ["host:port", ...]
            key_prefix: Prefix for all cache keys (default: 'fastapi_cachex:')

        Raises:
            CacheXError: If pymemcache is not installed
        """
        try:
            from pymemcache import HashClient
        except ImportError:
            raise CacheXError(
                "pymemcache is not installed. Please install it with 'pip install pymemcache'"
            )

        self.client = HashClient(servers, connect_timeout=5, timeout=5)
        self.key_prefix = key_prefix

    def _make_key(self, key: str) -> str:
        """Add prefix to cache key."""
        return f"{self.key_prefix}{key}"

    async def get(self, key: str) -> ETagContent | None:
        """Get value from cache.

        Args:
            key: Cache key to retrieve

        Returns:
            Optional[ETagContent]: Cached value with ETag if exists, None otherwise
        """
        prefixed_key = self._make_key(key)
        value = self.client.get(prefixed_key)
        if value is None:
            return None

        # Memcached stores data as bytes; deserialize from JSON
        try:
            data = json.loads(value.decode("utf-8"))
            return ETagContent(
                etag=data["etag"],
                content=data["content"].encode()
                if isinstance(data["content"], str)
                else data["content"],
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    async def set(self, key: str, value: ETagContent, ttl: int | None = None) -> None:
        """Set value in cache.

        Args:
            key: Cache key
            value: ETagContent to store
            ttl: Time to live in seconds
        """
        prefixed_key = self._make_key(key)

        # Prepare content for JSON serialization
        if isinstance(value.content, bytes):
            content = value.content.decode()
        else:
            content = value.content

        serialized_data: str | bytes = json.dumps(
            {
                "etag": value.etag,
                "content": content,
            }
        )

        # orjson returns bytes, stdlib json returns str
        serialized_bytes = (
            serialized_data
            if isinstance(serialized_data, bytes)
            else serialized_data.encode("utf-8")
        )

        self.client.set(
            prefixed_key,
            serialized_bytes,
            expire=ttl if ttl is not None else 0,
        )

    async def delete(self, key: str) -> None:
        """Delete value from cache.

        Args:
            key: Cache key to delete
        """
        self.client.delete(self._make_key(key))

    async def clear(self) -> None:
        """Clear all values from cache.

        Note: Memcached's flush_all affects the entire server.
        Consider using clear_path() with your specific keys instead.
        """
        warnings.warn(
            "Memcached.clear() flushes ALL cached data from the server, "
            "affecting other applications. Consider using clear_path() instead "
            "to selectively remove only this namespace's keys.",
            RuntimeWarning,
            stacklevel=2,
        )
        self.client.flush_all()

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path.

        Note: Memcached does not support pattern-based queries.
        This method can only delete keys if the exact key is provided,
        or will try to match keys in memory if include_params=True.
        For better pattern support, consider using Redis backend.

        Args:
            path: The path to clear cache for
            include_params: Currently unsupported (Memcached limitation)

        Returns:
            Number of cache entries cleared (0 or 1 for exact match only)
        """
        if include_params:
            warnings.warn(
                "Memcached backend does not support pattern-based key clearing. "
                "Only exact key matches can be deleted. "
                "The include_params option has no effect. "
                "Consider using Redis backend for pattern support.",
                RuntimeWarning,
                stacklevel=2,
            )

        # Try to delete the prefixed key (exact match only)
        prefixed_key = self._make_key(path)
        try:
            result = self.client.delete(prefixed_key, noreply=False)
            return 1 if result else 0
        except Exception:  # noqa: BLE001
            return 0

    async def clear_pattern(self, pattern: str) -> int:  # noqa: ARG002
        """Clear cached responses matching a pattern.

        Memcached does not support pattern matching or key scanning.
        This operation is not available.

        Args:
            pattern: A glob pattern (not supported by Memcached)

        Returns:
            Always 0, as pattern matching is not supported
        """
        warnings.warn(
            "Memcached backend does not support pattern matching. "
            "Pattern-based cache clearing is not available with Memcached. "
            "Consider using Redis backend for pattern support, "
            "or track keys manually in your application logic.",
            RuntimeWarning,
            stacklevel=2,
        )
        return 0
