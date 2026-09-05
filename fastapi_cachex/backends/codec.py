"""Serialization shared by the network backends (Redis, Memcached).

Both backends store a ``CacheEntry`` as a JSON document; ``orjson`` is used when
it is installed and the standard library ``json`` module otherwise.
"""

from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry

try:
    import orjson as json

except ImportError:  # pragma: no cover
    import json  # type: ignore[no-redef]  # pragma: no cover

# ``json.loads`` (either implementation) raises ``ValueError`` subclasses for bad
# JSON; ``KeyError``/``TypeError``/``AttributeError`` cover documents whose shape
# is not the one ``encode_entry`` writes (missing fields, non-string content).
_DECODE_ERRORS = (ValueError, KeyError, TypeError, AttributeError)


def encode_entry(entry: CacheEntry) -> bytes:
    """Serialize a ``CacheEntry`` to a UTF-8 JSON document.

    The raw content bytes are passed through ``latin-1`` so that arbitrary
    bytes round-trip through JSON text.
    """
    serialized: str | bytes = json.dumps(
        {
            "fingerprint": entry.fingerprint,
            "content": entry.content.decode("latin-1"),
            "media_type": entry.media_type,
        },
    )
    # orjson returns bytes, stdlib json returns str
    return serialized if isinstance(serialized, bytes) else serialized.encode("utf-8")


def _as_counter(raw: str | bytes) -> int | None:
    """The integer a bare counter value holds, or ``None`` for anything else."""
    try:
        return int(raw)
    except ValueError:
        return None


def decode_entry(raw: str | bytes | None) -> CacheEntry | None:
    """Rebuild a ``CacheEntry`` from a stored value.

    A bare integer (what the server-side ``INCR`` family leaves behind for
    ``increment``) becomes a counter entry. Anything else that is not a document
    written by ``encode_entry`` (corrupt JSON, missing fields, non-string
    content) yields ``None``, so callers can treat every malformed value as a
    cache miss.
    """
    if raw is None:
        return None
    counter = _as_counter(raw)
    if counter is not None:
        return counter_entry(counter)
    try:
        data = json.loads(raw)
        return CacheEntry(
            fingerprint=data["fingerprint"],
            content=data["content"].encode("latin-1"),
            media_type=data.get("media_type"),
        )
    except _DECODE_ERRORS:
        return None
