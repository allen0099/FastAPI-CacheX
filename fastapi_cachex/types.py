from dataclasses import dataclass
from typing import Any
from typing import Optional


@dataclass
class ETagContent:
    """ETag and content for cache items."""

    etag: str
    content: Any


@dataclass
class CacheItem:
    """Cache item with optional expiry time."""

    value: ETagContent
    expiry: Optional[int] = None
