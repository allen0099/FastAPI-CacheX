"""Type definitions and type aliases for FastAPI-CacheX."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

# Cache key separator - using ||| to avoid conflicts with port numbers in host (e.g., 127.0.0.1:8000)
CACHE_KEY_SEPARATOR = "|||"

# Type for custom cache key builder function
CacheKeyBuilder = Callable[[Request], str]


@dataclass
class ETagContent:
    """ETag and content for cache items."""

    etag: str
    content: Any


@dataclass
class CacheItem:
    """Cache item with optional expiry time.

    Args:
        value: The cached ETagContent
        expiry: Epoch timestamp when this cache item expires (None = never expires)
    """

    value: ETagContent
    expiry: float | None = None
