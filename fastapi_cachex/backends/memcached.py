import ast
import warnings
from typing import Optional

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import ETagContent


class MemcachedBackend(BaseCacheBackend):
    """Memcached backend implementation."""

    def __init__(self, servers: list[str]) -> None:
        """Initialize the Memcached backend.

        Args:
            servers: List of Memcached servers in format ["host:port", ...]

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

    async def get(self, key: str) -> Optional[ETagContent]:
        """Get value from cache.

        Args:
            key: Cache key to retrieve

        Returns:
            Optional[ETagContent]: Cached value with ETag if exists, None otherwise
        """
        value = self.client.get(key)
        if value is None:
            return None

        # Memcached stores data as bytes
        # Convert string back to dictionary
        value_dict = ast.literal_eval(value.decode("utf-8"))
        return ETagContent(etag=value_dict["etag"], content=value_dict["content"])

    async def set(
        self, key: str, value: ETagContent, ttl: Optional[int] = None
    ) -> None:
        """Set value in cache.

        Args:
            key: Cache key
            value: ETagContent to store
            ttl: Time to live in seconds
        """
        # Store as dictionary in string format
        data = {"etag": value.etag, "content": value.content}
        self.client.set(
            key, str(data).encode("utf-8"), expire=ttl if ttl is not None else 0
        )

    async def delete(self, key: str) -> None:
        """Delete value from cache.

        Args:
            key: Cache key to delete
        """
        self.client.delete(key)

    async def clear(self) -> None:
        """Clear all values from cache."""
        self.client.flush_all()

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path."""
        if include_params:
            warnings.warn(
                "Memcached backend does not support pattern-based key clearing. "
                "The include_params option will have no effect.",
                RuntimeWarning,
                stacklevel=2,
            )
            return 0

        # If we're not including params, we can just try to delete the exact path
        if self.client.delete(path, noreply=False):
            return 1
        return 0

    async def clear_pattern(self, pattern: str) -> int:  # noqa: ARG002
        """Clear cached responses matching a pattern."""
        warnings.warn(
            "Memcached backend does not support pattern matching. "
            "Pattern-based cache clearing is not available.",
            RuntimeWarning,
            stacklevel=2,
        )
        return 0
