"""Serialization shared by the network backends (Redis, Memcached).

Both backends store a ``CacheEntry`` as a JSON document; ``orjson`` is used when
it is installed and the standard library ``json`` module otherwise. A backend
built with ``compress_min_size`` stores a document of at least that many bytes
compressed with zlib, behind ``COMPRESSED_PREFIX``; every reader understands
both forms, whatever its own setting.
"""

import math
import zlib

from fastapi_cachex.types import COUNTER_FINGERPRINT
from fastapi_cachex.types import DEFAULT_STATUS_CODE
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry
from fastapi_cachex.types import parse_counter

try:
    import orjson as json

# Resolved once at import time; whichever branch this interpreter takes, the
# other one cannot be reached again in the same process.
except ImportError:  # pragma: no cover - import-time, environment dependent
    import json  # type: ignore[no-redef]

# ``json.loads`` (either implementation) raises ``ValueError`` subclasses for bad
# JSON; ``KeyError``/``TypeError``/``AttributeError`` cover documents whose shape
# is not the one ``encode_entry`` writes (missing fields, non-string content);
# fields of another type are checked in ``decode_entry``.
# The standard library parser raises ``RecursionError`` on deep nesting.
# ``zlib.error`` is a compressed document that was cut short or damaged.
_DECODE_ERRORS = (
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    RecursionError,
    zlib.error,
)

# Starts a compressed document. A JSON document cannot start with a NUL byte,
# and neither can a counter.
COMPRESSED_PREFIX = b"\x00zlib\x00"
# Fastest level: on typical JSON it gives most of the reduction of the
# default level (to about 22% against 19%) in less than half the time.
_COMPRESSION_LEVEL = 1


def validate_compress_min_size(value: object) -> int | None:
    """``compress_min_size`` for a backend: ``None``, or a positive ``int``."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = (
            "compress_min_size must be a number of bytes or None, "
            f"got {type(value).__name__}"
        )
        raise TypeError(msg)
    if value < 1:
        msg = f"compress_min_size must be greater than zero, got {value!r}"
        raise ValueError(msg)
    return value


def encode_entry(entry: CacheEntry, compress_min_size: int | None = None) -> bytes:
    """Serialize a ``CacheEntry`` to a UTF-8 JSON document.

    The raw content bytes are passed through ``latin-1`` so that arbitrary
    bytes round-trip through JSON text.

    A counter entry (exactly what ``counter_entry`` builds) is stored as a bare
    integer instead, the form the server-side ``INCR`` family works on, so a
    counter written with ``set()`` can still be passed to ``increment()``.

    With ``compress_min_size``, a document of at least that many bytes is
    returned compressed.
    """
    if entry.fingerprint == COUNTER_FINGERPRINT:
        value = parse_counter(entry.content)
        if value is not None and entry == counter_entry(value):
            return entry.content
    document: dict[str, object] = {
        "fingerprint": entry.fingerprint,
        "content": entry.content.decode("latin-1"),
        "media_type": entry.media_type,
        "status_code": entry.status_code,
        "headers": [[name, value] for name, value in entry.headers],
        "stored_at": entry.stored_at,
    }
    if entry.tags:
        # Only when there are any: an entry without tags is stored as before.
        document["tags"] = dict(entry.tags)
    if entry.stale_at is not None:
        document["stale_at"] = entry.stale_at
    serialized: str | bytes = json.dumps(document)
    # orjson returns bytes, stdlib json returns str
    encoded = (
        serialized if isinstance(serialized, bytes) else serialized.encode("utf-8")
    )
    if compress_min_size is not None and len(encoded) >= compress_min_size:
        # Even content that is compressed already shrinks: as JSON text its
        # bytes above 0x7f take two bytes or an escape each.
        return COMPRESSED_PREFIX + zlib.compress(encoded, _COMPRESSION_LEVEL)
    return encoded


# The status codes HTTP defines; anything else cannot be replayed.
_MIN_STATUS = 100
_MAX_STATUS = 599


def _stored_at(value: object) -> float | None:
    """``stored_at`` from a document; anything but a finite number is unknown."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _tags(value: object) -> tuple[tuple[str, str], ...]:
    """``tags`` from a document: ``{name: version}``, both strings.

    Anything else raises, so the document is a miss: an entry whose tags
    cannot be read must not be served as if it had none.
    """
    if value is None:
        return ()
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(version, str)
        for name, version in value.items()
    ):
        msg = "tags must map tag names to versions"
        raise TypeError(msg)
    return tuple(value.items())


def decode_entry(raw: str | bytes | None) -> CacheEntry | None:
    """Rebuild a ``CacheEntry`` from a stored value.

    A bare integer (what the server-side ``INCR`` family leaves behind for
    ``increment``) becomes a counter entry. Anything else that is not a document
    written by ``encode_entry`` (corrupt JSON, missing fields, a field of the
    wrong type, a status code outside 100-599) yields ``None``, so callers can treat every malformed value as a
    cache miss.

    Documents written before entries carried a status code and headers simply
    lack those keys and decode to a plain ``200`` with no extra headers; those
    written before entries carried ``stored_at`` decode with ``None``, and
    those without ``tags`` or ``stale_at`` with none. Headers
    are a list of ``[name, value]`` lines; the object 0.3.x wrote (one value
    per name) is still read.
    """
    if raw is None:
        return None
    counter = parse_counter(raw)
    if counter is not None:
        return counter_entry(counter)
    try:
        if isinstance(raw, bytes) and raw.startswith(COMPRESSED_PREFIX):
            raw = zlib.decompress(raw[len(COMPRESSED_PREFIX) :])
        data = json.loads(raw)
        fingerprint = data["fingerprint"]
        media_type = data.get("media_type")
        status_code = data.get("status_code", DEFAULT_STATUS_CODE)
        if (
            not isinstance(fingerprint, str)
            or not isinstance(media_type, (str, type(None)))
            or isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not _MIN_STATUS <= status_code <= _MAX_STATUS
        ):
            return None
        return CacheEntry(
            fingerprint=fingerprint,
            content=data["content"].encode("latin-1"),
            media_type=media_type,
            status_code=status_code,
            headers=data.get("headers"),
            stored_at=_stored_at(data.get("stored_at")),
            tags=_tags(data.get("tags")),
            stale_at=_stored_at(data.get("stale_at")),
        )
    except _DECODE_ERRORS:
        return None
