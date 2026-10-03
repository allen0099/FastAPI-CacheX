"""Storing a response in a `CacheEntry` and replaying it, ETags and 304s."""

import hashlib
import time
from collections.abc import Iterable
from collections.abc import Mapping

from fastapi import Response
from starlette.status import HTTP_200_OK
from starlette.status import HTTP_206_PARTIAL_CONTENT
from starlette.status import HTTP_300_MULTIPLE_CHOICES
from starlette.status import HTTP_304_NOT_MODIFIED

from .types import CacheEntry
from .types import HeaderPairs

# Wall clock behind ``CacheEntry.stored_at`` and the ``Age`` header. Wall time,
# not monotonic, because an entry stored by one process or host is served by
# another. A module attribute so tests can move time without sleeping.
_now = time.time


# Headers that must never be replayed from cache. ``set-cookie`` carries
# per-user state, ``content-type`` is already held by ``CacheEntry.media_type``
# (storing both would emit the header twice), and the rest are either
# connection-scoped or rebuilt for every response.
_UNCACHEABLE_HEADERS = frozenset(
    {
        "set-cookie",
        "content-length",
        "transfer-encoding",
        "connection",
        "date",
        "etag",
        "cache-control",
        "content-type",
        "age",
    }
)


def _is_cacheable_status(status_code: int) -> bool:
    """Whether a response with this status may be stored and replayed.

    Only successful responses are cacheable here. ``206 Partial Content`` is
    excluded because its body is meaningful only for the ``Range`` request that
    produced it, so replaying it to another request would corrupt the response.
    """
    return (
        HTTP_200_OK <= status_code < HTTP_300_MULTIPLE_CHOICES
        and status_code != HTTP_206_PARTIAL_CONTENT
    )


def _cacheable_headers(response: Response) -> HeaderPairs:
    """The handler's own header lines worth storing, in the order sent.

    Every line is kept, so a header sent more than once (several ``Link``
    lines) is replayed line by line rather than as its last value.
    """
    return tuple(
        (name, value)
        for name, value in response.headers.items()
        if name.lower() not in _UNCACHEABLE_HEADERS
    )


def _append_headers(response: Response, headers: Iterable[tuple[str, str]]) -> None:
    """Add every ``(name, value)`` line to ``response``, duplicates included."""
    for name, value in headers:
        response.headers.append(name, value)


# Fields RFC 9110 §15.4.5 asks a 304 to repeat from the 200 it stands in for.
# ``Date`` comes from Starlette, ``ETag`` and ``Cache-Control`` are set on the
# 304 directly, which leaves these three to be carried over.
_REVALIDATION_HEADERS = frozenset({"content-location", "expires", "vary"})


def _age_headers(entry: CacheEntry, ttl: int | None) -> dict[str, str]:
    """The ``Age`` header for a response served from a stored ``entry``.

    ``Cache-Control`` keeps ``max-age=<ttl>`` on a hit: RFC 9111 §4.2.3 has a
    downstream cache compute the remaining freshness as ``max-age`` minus
    ``Age``, so a copy stored here N seconds ago is fresh downstream for
    ``ttl - N`` more seconds, and the total never reaches twice the ttl.

    ``Age`` is a non-negative integer number of seconds (RFC 9111 §5.1). The
    value is clamped to ``[0, ttl]``: ``stored_at`` may come from another
    host's clock, and the backend never keeps an entry longer than ``ttl``, so
    anything outside that range is clock skew. An entry without ``stored_at``
    (written by an older release) gets no ``Age`` at all.
    """
    if entry.stored_at is None:
        return {}
    age = max(0.0, _now() - entry.stored_at)
    if ttl is not None:
        age = min(age, ttl)
    return {"age": str(int(age))}


def _revalidation_headers(headers: Iterable[tuple[str, str]]) -> HeaderPairs:
    """The lines of a response's headers that a 304 must repeat."""
    return tuple(
        (name, value)
        for name, value in headers
        if name.lower() in _REVALIDATION_HEADERS
    )


def _media_type_of(response: Response) -> str | None:
    """The response media type, falling back to a directly-set Content-Type."""
    if response.media_type is not None:
        return response.media_type
    return response.headers.get("content-type")


def _get_response_body(response: Response) -> bytes | None:
    """Return response body bytes, or None for streaming/file responses."""
    return getattr(response, "body", None)


def _etag_for(body: bytes) -> str:
    return f'W/"{hashlib.md5(body).hexdigest()}"'  # noqa: S324


def _weak_etag(etag: str) -> str:
    """An ETag reduced to its opaque tag, so weak and strong forms compare equal."""
    tag = etag.strip()
    if tag[:2].upper() == "W/":
        tag = tag[2:]
    return tag.strip('"')


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` header selects ``etag`` (RFC 9110 §8.8.3.2).

    If-None-Match uses the weak comparison function, so the ``W/`` prefix is
    ignored on both sides, and it may list several validators. ``*`` matches
    whenever the resource exists, which every caller has already established
    before asking.

    Candidates are split on commas. That misreads an opaque-tag containing a
    literal comma, which this library never generates and RFC 9110 treats as
    pathological; the simpler split is worth the edge case.
    """
    if not if_none_match:
        return False

    header = if_none_match.strip()
    if header == "*":
        return True

    target = _weak_etag(etag)
    return any(_weak_etag(candidate) == target for candidate in header.split(","))


def _not_modified(
    etag: str,
    cache_control: str,
    headers: Iterable[tuple[str, str]] = (),
    age: Mapping[str, str] | None = None,
) -> Response:
    """Build the 304 for a successful revalidation.

    ``headers`` is the header lines the 200 for this resource would have
    carried, every line of a repeated field included; RFC 9110
    §15.4.5 requires the fields that steer caching to be repeated on the 304,
    otherwise a cache that stored the 200 would drop them on refresh. ``Date``
    is added by Starlette and the other two are set here. ``age`` is the
    ``Age`` header (see ``_age_headers``) when the 304 is answered from a
    stored entry.
    """
    response = Response(status_code=HTTP_304_NOT_MODIFIED)
    _append_headers(response, _revalidation_headers(headers))
    response.headers["ETag"] = etag
    if cache_control:
        response.headers["Cache-Control"] = cache_control
    response.headers.update(age or {})
    return response


def _fresh_not_modified(
    response: Response, etag: str, client_etag: str | None, cache_control: str
) -> Response | None:
    """The 304 for a response the handler just rendered, or None to send it.

    None unless ``client_etag`` matches. The handler ran, so what it did
    beyond the body still reaches the client: its ``Set-Cookie`` lines are
    repeated on the 304 (such a response is never stored, and
    ``_cache_control_for`` marks it ``private``), and its ``background`` task
    moves onto the 304 (#233).
    """
    if not _etag_matches(client_etag, etag):
        return None
    not_modified = _not_modified(etag, cache_control, response.headers.items())
    _append_headers(
        not_modified,
        [(k, v) for k, v in response.headers.items() if k.lower() == "set-cookie"],
    )
    not_modified.background = response.background
    return not_modified


def _entry_for(response: Response, etag: str, body: bytes) -> CacheEntry:
    """The ``CacheEntry`` that stores ``response``, stamped with ``_now()``."""
    return CacheEntry(
        fingerprint=etag,
        content=body,
        media_type=_media_type_of(response),
        status_code=response.status_code,
        headers=_cacheable_headers(response),
        stored_at=_now(),
    )
