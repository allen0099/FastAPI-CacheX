"""Type definitions and type aliases for FastAPI-CacheX."""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from fastapi_cachex.exceptions import CacheXError

# Separates the components of an HTTP cache key. Every client-controlled
# component is percent-encoded first (see ``escape_key_component``), so a
# single ``|`` is enough; the query string is URL-encoded and never has one.
CACHE_KEY_SEPARATOR = "|"

# First component of every HTTP cache key (see ``CacheKey``). 0.3.x keys had no
# tag and used ``|||``; the next format change bumps it.
HTTP_KEY_FORMAT_TAG = "http:v2"

# Type for custom cache key builder function
CacheKeyBuilder = Callable[[Request], str]

_KEY_ESCAPES = {"%": "%25", "|": "%7C"}
_KEY_UNESCAPES = {escaped: char for char, escaped in _KEY_ESCAPES.items()}
_KEY_UNESCAPE_RE = re.compile("%25|%7C")


def escape_key_component(value: str) -> str:
    """Percent-encode ``|`` and ``%`` so ``value`` cannot contain the separator.

    The host header and the decoded URL path are client-controlled and may
    contain ``|``; left as is, one request's components could line up into
    another request's key. Encoding ``%`` as well keeps the mapping reversible,
    so two different values never share an encoding.
    """
    return value.replace("%", "%25").replace("|", "%7C")


def unescape_key_component(value: str) -> str:
    """Reverse ``escape_key_component``."""
    return _KEY_UNESCAPE_RE.sub(lambda match: _KEY_UNESCAPES[match.group()], value)


def log_ref(value: str) -> str:
    """Return a short digest that identifies ``value`` in logs without revealing it.

    Cache keys carry the raw query string, ``vary`` header values and custom
    key components, and OAuth states come from the callback query string;
    logging them at ``WARNING`` would leak tokens, e-mail addresses or user
    IDs into application logs. The same value always gives the same digest,
    so a warning can still be matched to the full value logged at ``DEBUG``.
    """
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:12]


# Status replayed for entries stored before ``CacheEntry`` carried a status code.
DEFAULT_STATUS_CODE = 200


@dataclass
class CacheEntry:
    """A cached response: fingerprint, raw body bytes, and how to replay it.

    ``status_code`` and ``headers`` default to a plain ``200`` with no extra
    headers, so entries built by older callers (and documents written by older
    releases) keep their previous behaviour.

    ``stored_at`` is when ``@cache`` stored the response, in epoch seconds
    from the wall clock (``time.time()``), since an entry written by one
    process or host may be served by another. It drives the ``Age`` header on
    a hit; ``None`` (entries written by older releases, and anything not
    stored by ``@cache``) sends no ``Age``.
    """

    fingerprint: str
    content: bytes
    media_type: str | None = None
    status_code: int = DEFAULT_STATUS_CODE
    headers: dict[str, str] | None = None
    stored_at: float | None = None


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
