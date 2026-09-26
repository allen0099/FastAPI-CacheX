"""Type definitions and type aliases for FastAPI-CacheX."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from fastapi_cachex.exceptions import CacheXError

# Cache key separator - using ||| to avoid conflicts with port numbers in host (e.g., 127.0.0.1:8000)
CACHE_KEY_SEPARATOR = "|||"

# Type for custom cache key builder function
CacheKeyBuilder = Callable[[Request], str]


# Status replayed for entries stored before ``CacheEntry`` carried a status code.
DEFAULT_STATUS_CODE = 200


@dataclass
class CacheEntry:
    """A cached response: fingerprint, raw body bytes, and how to replay it.

    ``status_code`` and ``headers`` default to a plain ``200`` with no extra
    headers, so entries built by older callers (and documents written by older
    releases) keep their previous behaviour.
    """

    fingerprint: str
    content: bytes
    media_type: str | None = None
    status_code: int = DEFAULT_STATUS_CODE
    headers: dict[str, str] | None = None


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


# Trailing spaces are allowed because Memcached pads a value that DECR made
# shorter instead of resizing it (DECR on "10" leaves "9 ").
_COUNTER_PATTERN = re.compile(rb"-?[0-9]+ *")


def parse_counter(raw: str | bytes) -> int | None:
    """The integer ``raw`` spells as a plain decimal, or ``None`` otherwise.

    Stricter than ``int()``: leading whitespace, underscores, a ``+`` sign and
    non-ASCII digits are rejected. Only the shapes the server-side ``INCR``
    family of Redis and Memcached leaves behind are accepted.
    """
    data = raw.encode() if isinstance(raw, str) else raw
    return int(data) if _COUNTER_PATTERN.fullmatch(data) else None


def counter_value(entry: CacheEntry) -> int:
    """Read the integer a counter entry holds.

    Raises:
        CacheXError: If the entry is not a counter, e.g. the key holds a
            cached response, even one whose body is a number.
    """
    value = parse_counter(entry.content)
    if entry.fingerprint != COUNTER_FINGERPRINT or value is None:
        msg = "Cache key holds a value that is not a counter"
        raise CacheXError(msg)
    return value
