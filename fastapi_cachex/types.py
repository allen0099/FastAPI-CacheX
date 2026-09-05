"""Type definitions and type aliases for FastAPI-CacheX."""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from fastapi_cachex.exceptions import CacheXError

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


# Fingerprint every backend reports for a key that holds an integer counter
# (see ``BaseCacheBackend.increment``). Counters surface through ``get()`` as a
# ``CacheEntry`` whose content is the decimal value, so delete/clear/monitoring
# treat them like any other entry.
COUNTER_FINGERPRINT = "counter"


def counter_entry(value: int) -> CacheEntry:
    """Wrap an integer counter in the entry model shared by every backend."""
    return CacheEntry(fingerprint=COUNTER_FINGERPRINT, content=str(value).encode())


def counter_value(entry: CacheEntry) -> int:
    """Read the integer a counter entry holds.

    Raises:
        CacheXError: If the content is not a decimal integer, i.e. the key
            holds a cached response rather than a counter.
    """
    try:
        return int(entry.content)
    except ValueError as e:
        msg = "Cache key holds a value that is not a counter"
        raise CacheXError(msg) from e
