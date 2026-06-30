"""Type definitions and type aliases for FastAPI-CacheX."""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

# Cache key separator - using ||| to avoid conflicts with port numbers in host (e.g., 127.0.0.1:8000)
CACHE_KEY_SEPARATOR = "|||"

# Type for custom cache key builder function
CacheKeyBuilder = Callable[[Request], str]


@dataclass
class CacheEntry:
    """Cache entry storing a fingerprint, raw content bytes, and an optional media type."""

    fingerprint: str
    content: bytes
    media_type: str | None = None


@dataclass
class CacheItem:
    """Cache item with optional expiry time.

    Args:
        value: The cached entry
        expiry: Epoch timestamp when this cache item expires (None = never expires)
    """

    value: CacheEntry
    expiry: float | None = None
