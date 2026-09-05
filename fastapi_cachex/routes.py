"""Optional routes for cache monitoring and management."""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

from .exceptions import BackendNotFoundError
from .proxy import BackendProxy
from .types import CACHE_KEY_SEPARATOR
from .types import CacheEntry

if TYPE_CHECKING:
    from fastapi import FastAPI

# Constants
CACHE_KEY_MIN_PARTS = 3
CACHE_KEY_MAX_PARTS = 3
_PREVIEW_BYTES = 100


@dataclass
class CacheHitRecord:
    """Record for a single cache hit."""

    cache_key: str
    method: str
    host: str
    path: str
    query_params: str
    etag: str
    is_expired: bool
    ttl_remaining: float | None


@dataclass
class CacheHitSummary:
    """Summary of cache hit statistics."""

    total_cached_entries: int
    active_entries: int
    cached_paths: list[str]


@dataclass
class CacheHitsResponse:
    """Response for cached hits endpoint."""

    cached_hits: list[CacheHitRecord]
    total_hits: int
    valid_hits: int
    expired_hits: int
    unique_routes: int
    summary: CacheHitSummary


@dataclass
class CachedRecord:
    """Record for a single cached item."""

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
    content_preview: str


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


def _parse_cache_key(cache_key: str) -> tuple[str, str, str, str]:
    """Parse cache key into components.

    Args:
        cache_key: Cache key in format method|||host|||path|||query_params

    Returns:
        Tuple of (method, host, path, query_params)
    """
    key_parts = cache_key.split(CACHE_KEY_SEPARATOR, CACHE_KEY_MAX_PARTS)
    if len(key_parts) >= CACHE_KEY_MIN_PARTS:
        method, host, path = key_parts[0], key_parts[1], key_parts[2]
        query_params = key_parts[3] if len(key_parts) > CACHE_KEY_MIN_PARTS else ""
        return method, host, path, query_params

    return "", "", "", ""


@dataclass
class _Entry:
    """One parsed backend entry, shared by both monitoring views."""

    cache_key: str
    method: str
    host: str
    path: str
    query_params: str
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
        method, host, path, query_params = _parse_cache_key(cache_key)
        if not method:
            continue
        entries.append(
            _Entry(
                cache_key=cache_key,
                method=method,
                host=host,
                path=path,
                query_params=query_params,
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
    cached_hits = [
        CacheHitRecord(
            cache_key=e.cache_key,
            method=e.method,
            host=e.host,
            path=e.path,
            query_params=e.query_params,
            etag=e.entry.fingerprint,
            is_expired=e.is_expired,
            ttl_remaining=e.ttl_remaining,
        )
        for e in entries
    ]
    valid_hits = [h for h in cached_hits if not h.is_expired]
    routes_hit = {h.path for h in valid_hits}

    return CacheHitsResponse(
        cached_hits=cached_hits,
        total_hits=len(cached_hits),
        valid_hits=len(valid_hits),
        expired_hits=len(cached_hits) - len(valid_hits),
        unique_routes=len(routes_hit),
        summary=CacheHitSummary(
            total_cached_entries=len(cached_hits),
            active_entries=len(valid_hits),
            cached_paths=sorted(routes_hit),
        ),
    )


def _cached_records(entries: list[_Entry]) -> CachedRecordsResponse:
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
            content_preview=e.entry.content[:_PREVIEW_BYTES].decode(
                "utf-8", errors="ignore"
            ),
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
    dependencies: Sequence[Any] | None = None,
) -> None:
    """Add cache monitoring routes to the FastAPI application.

    This function allows users to optionally add cache monitoring routes
    to their FastAPI application. Users can call this function to enable
    cache hit tracking and cache record display.

    Args:
        app: FastAPI application instance
        prefix: URL prefix for the routes (e.g., "/api/cache", "/admin/cache").
                Defaults to "" (no prefix).
        include_in_schema: Whether to include routes in OpenAPI schema.
                          Defaults to False.
        dependencies: Optional list of FastAPI ``Depends`` objects applied to
                      all monitoring routes.  Useful for adding authentication
                      or authorization guards (e.g.
                      ``[Depends(verify_api_key)]``).

    Example:
        from fastapi import FastAPI
        from fastapi_cachex import add_routes

        app = FastAPI()
        add_routes(app)  # Routes at /cached-hits and /cached-records

        # Or with prefix
        add_routes(app, prefix="/api/cache")  # Routes at /api/cache/cached-hits and /api/cache/cached-records
    """

    @app.get(
        f"{prefix}/cached-hits",
        include_in_schema=include_in_schema,
        dependencies=dependencies,
    )
    async def get_cached_hits() -> CacheHitsResponse:
        """Return cached hit records.

        Shows cache statistics including which routes are frequently being cached,
        hit counts, and cache key information.

        Returns:
            CacheHitsResponse containing cache hit records and statistics
        """
        return _cached_hits(_parse_entries(await _cache_data()))

    @app.get(
        f"{prefix}/cached-records",
        include_in_schema=include_in_schema,
        dependencies=dependencies,
    )
    async def get_cached_records() -> CachedRecordsResponse:
        """Display currently cached records.

        Returns all currently cached records in the cache backend with their
        content information and expiry details.

        Returns:
            CachedRecordsResponse containing cached records and statistics
        """
        return _cached_records(_parse_entries(await _cache_data()))
