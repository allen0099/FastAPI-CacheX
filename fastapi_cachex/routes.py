"""Optional routes for cache monitoring and management."""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from .cache_key import CacheKey
from .exceptions import BackendNotFoundError
from .proxy import BackendProxy
from .types import CacheEntry

if TYPE_CHECKING:
    from fastapi import FastAPI

_PREVIEW_BYTES = 100


@dataclass
class CacheHitRecord:
    """One cached route entry. Despite the name, hits are not counted."""

    cache_key: str
    method: str
    host: str
    path: str
    query_params: str
    etag: str
    is_expired: bool
    ttl_remaining: float | None
    extra_components: list[str] = field(default_factory=list)


@dataclass
class CacheHitSummary:
    """Summary of the cached route entries."""

    total_cached_entries: int
    active_entries: int
    cached_paths: list[str]


@dataclass
class CacheHitsResponse:
    """Response for the ``/cached-hits`` endpoint.

    ``entries``, ``total_entries``, ``valid_entries`` and ``expired_entries``
    list and count the cached route entries. Despite the route's name, hits
    are not counted.
    """

    entries: list[CacheHitRecord]
    total_entries: int
    valid_entries: int
    expired_entries: int
    unique_routes: int
    summary: CacheHitSummary


@dataclass
class CachedRecord:
    """Record for a single cached item.

    ``media_type`` is the stored response's media type (``None`` when it had
    none). ``content_type`` is always ``"bytes"``, the type of the stored body,
    and is kept for compatibility. ``extra_components`` holds the components a
    key builder appended after the query string (see ``build_cache_key``),
    decoded.
    """

    cache_key: str
    method: str
    host: str
    path: str
    query_params: str
    etag: str
    content_type: str
    content_size: int
    is_expired: bool
    ttl_remaining: float | None
    content_preview: str | None
    media_type: str | None
    extra_components: list[str] = field(default_factory=list)


@dataclass
class CacheSummary:
    """Summary of cached records."""

    total_entries: int
    valid_entries: int
    estimated_cache_size_kb: float


@dataclass
class CachedRecordsResponse:
    """Response for cached records endpoint."""

    cached_records: list[CachedRecord]
    total_records: int
    active_records: int
    expired_records: int
    total_cache_size_bytes: int
    summary: CacheSummary


@dataclass
class _Entry:
    """One parsed backend entry, shared by both monitoring views."""

    cache_key: str
    method: str
    host: str
    path: str
    query_params: str
    extra_components: list[str]
    entry: CacheEntry
    is_expired: bool
    ttl_remaining: float | None


def _parse_entries(
    cache_data: dict[str, tuple[CacheEntry, float | None]],
) -> list[_Entry]:
    """Parse the backend dump into entries, skipping keys that are not route keys."""
    now = time.time()
    entries: list[_Entry] = []
    for cache_key, (entry, expiry) in cache_data.items():
        key = CacheKey.parse(cache_key)
        if key is None:
            continue
        entries.append(
            _Entry(
                cache_key=cache_key,
                method=key.method,
                host=key.host,
                path=key.path,
                query_params=key.query,
                extra_components=list(key.extra),
                entry=entry,
                is_expired=expiry is not None and expiry <= now,
                ttl_remaining=(
                    max(0.0, round(expiry - now, 2)) if expiry is not None else None
                ),
            )
        )
    return entries


async def _cache_data() -> dict[str, tuple[CacheEntry, float | None]]:
    """The configured backend's dump, or nothing when no backend is configured."""
    try:
        backend = BackendProxy.get()
    except BackendNotFoundError:
        return {}
    return await backend.get_cache_data()


def _cached_hits(entries: list[_Entry]) -> CacheHitsResponse:
    records = [
        CacheHitRecord(
            cache_key=e.cache_key,
            method=e.method,
            host=e.host,
            path=e.path,
            query_params=e.query_params,
            etag=e.entry.fingerprint,
            is_expired=e.is_expired,
            ttl_remaining=e.ttl_remaining,
            extra_components=e.extra_components,
        )
        for e in entries
    ]
    valid = sum(1 for r in records if not r.is_expired)
    valid_paths = {r.path for r in records if not r.is_expired}
    total = len(records)
    return CacheHitsResponse(
        entries=records,
        total_entries=total,
        valid_entries=valid,
        expired_entries=total - valid,
        unique_routes=len(valid_paths),
        summary=CacheHitSummary(
            total_cached_entries=total,
            active_entries=valid,
            cached_paths=sorted(valid_paths),
        ),
    )


def _cached_records(
    entries: list[_Entry], include_content_preview: bool
) -> CachedRecordsResponse:
    cached_records = [
        CachedRecord(
            cache_key=e.cache_key,
            method=e.method,
            host=e.host,
            path=e.path,
            query_params=e.query_params,
            etag=e.entry.fingerprint,
            content_type="bytes",
            content_size=len(e.entry.content),
            is_expired=e.is_expired,
            ttl_remaining=e.ttl_remaining,
            content_preview=(
                e.entry.content[:_PREVIEW_BYTES].decode("utf-8", errors="ignore")
                if include_content_preview
                else None
            ),
            media_type=e.entry.media_type,
            extra_components=e.extra_components,
        )
        for e in entries
    ]
    active_records = sum(1 for r in cached_records if not r.is_expired)
    total_size = sum(r.content_size for r in cached_records)

    return CachedRecordsResponse(
        cached_records=cached_records,
        total_records=len(cached_records),
        active_records=active_records,
        expired_records=len(cached_records) - active_records,
        total_cache_size_bytes=total_size,
        summary=CacheSummary(
            total_entries=len(cached_records),
            valid_entries=active_records,
            estimated_cache_size_kb=round(total_size / 1024, 2),
        ),
    )


def add_routes(
    app: "FastAPI",
    prefix: str = "",
    include_in_schema: bool = False,
    *,
    dependencies: Sequence[Any],
    include_content_preview: bool = False,
) -> None:
    """Add cache monitoring routes to the FastAPI application.

    Mounts two read-only routes that report what the configured backend
    currently holds. They inspect stored entries; nothing counts cache hits.
    The routes have no authentication of their own, so ``dependencies`` is
    required: pass your guard, or ``dependencies=[]`` to mount the routes
    unguarded on purpose (local or test setups).

    Args:
        app: FastAPI application instance
        prefix: URL prefix for the routes (e.g., "/api/cache", "/admin/cache").
                Defaults to "" (no prefix).
        include_in_schema: Whether to include routes in OpenAPI schema.
                          Defaults to False.
        dependencies: FastAPI ``Depends`` objects applied to all monitoring
                      routes, for authentication or authorization guards
                      (e.g. ``[Depends(verify_api_key)]``). Required and
                      keyword-only; an empty list mounts the routes unguarded.
        include_content_preview: Whether ``/cached-records`` includes the first
                      100 bytes of each cached response body. Defaults to
                      False, which reports ``content_preview`` as ``null``
                      while keys, sizes and expiry are still reported.

    Raises:
        TypeError: If ``dependencies`` is ``None``.

    Example:
        ```python
        from fastapi import Depends, FastAPI
        from fastapi_cachex import add_routes

        app = FastAPI()
        # Routes at /api/cache/cached-hits and /api/cache/cached-records,
        # guarded by your own dependency
        add_routes(app, prefix="/api/cache", dependencies=[Depends(verify_admin)])
        ```
    """
    if dependencies is None:
        # Unreachable for type checkers; guards untyped callers.
        msg = (  # type: ignore[unreachable]
            "add_routes() requires dependencies: pass "
            "dependencies=[Depends(your_auth)] to guard the cache monitoring "
            "routes, or dependencies=[] to mount them unguarded on purpose "
            "(https://github.com/allen0099/FastAPI-CacheX/issues/298)"
        )
        raise TypeError(msg)

    @app.get(
        f"{prefix}/cached-hits",
        include_in_schema=include_in_schema,
        dependencies=dependencies,
    )
    async def get_cached_hits() -> CacheHitsResponse:
        """List the cached route entries.

        Splits every cached route key (other keys are skipped) into
        method, host, path, query and any
        extra components a key builder appended, with its
        ETag and expiry, plus counts of valid and expired entries and the
        distinct cached paths. Cache hits are not counted.

        Returns:
            CacheHitsResponse describing the cached entries
        """
        return _cached_hits(_parse_entries(await _cache_data()))

    @app.get(
        f"{prefix}/cached-records",
        include_in_schema=include_in_schema,
        dependencies=dependencies,
    )
    async def get_cached_records() -> CachedRecordsResponse:
        """Display currently cached records.

        Returns every route entry in the cache backend (keys in the
        ``http:v2|method|host|path|query`` format; other keys are skipped) with
        its content information and expiry details.

        Returns:
            CachedRecordsResponse containing cached records and statistics
        """
        return _cached_records(
            _parse_entries(await _cache_data()), include_content_preview
        )
