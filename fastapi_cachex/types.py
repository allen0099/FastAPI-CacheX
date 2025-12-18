"""Type definitions and type aliases for FastAPI-CacheX."""

from dataclasses import dataclass
from typing import Any


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
