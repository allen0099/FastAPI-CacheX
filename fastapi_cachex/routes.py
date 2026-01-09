"""Optional routes for cache monitoring and management."""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .backends import BaseCacheBackend
from .exceptions import BackendNotFoundError
from .proxy import BackendProxy
from .types import CACHE_KEY_SEPARATOR

if TYPE_CHECKING:
    from fastapi import FastAPI

# Constants
CACHE_KEY_MIN_PARTS = 3
CACHE_KEY_MAX_PARTS = 3


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
    frequently_cached_routes: list[str]


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


async def _get_cached_hits_handler(backend: BaseCacheBackend) -> CacheHitsResponse:
    """Handle the cached hits request.

    Args:
        backend: The cache backend instance

    Returns:
        CacheHitsResponse
    """
    cache_data = await backend.get_cache_data()

    now = time.time()
    cached_hits: list[CacheHitRecord] = []

    for cache_key, (etag_content, expiry) in cache_data.items():
        method, host, path, query_params = _parse_cache_key(cache_key)
        if method:  # Valid cache key
            # Check if cache entry is expired
            is_expired = expiry is not None and expiry <= now
            ttl_remaining = round(expiry - now, 2) if expiry is not None else None

            cached_hits.append(
                CacheHitRecord(
                    cache_key=cache_key,
                    method=method,
                    host=host,
                    path=path,
                    query_params=query_params,
                    etag=etag_content.etag,
                    is_expired=is_expired,
                    ttl_remaining=ttl_remaining,
                )
            )

    # Calculate summary statistics
    valid_hits: list[CacheHitRecord] = [h for h in cached_hits if not h.is_expired]
    routes_hit: set[str] = {h.path for h in valid_hits}

    return CacheHitsResponse(
        cached_hits=cached_hits,
        total_hits=len(cached_hits),
        valid_hits=len(valid_hits),
        expired_hits=len(cached_hits) - len(valid_hits),
        unique_routes=len(routes_hit),
        summary=CacheHitSummary(
            total_cached_entries=len(cached_hits),
            active_entries=len(valid_hits),
            frequently_cached_routes=sorted(routes_hit),
        ),
    )


async def _get_cached_records_handler(
    backend: BaseCacheBackend,
) -> CachedRecordsResponse:
    """Handle the cached records request.

    Args:
        backend: The cache backend instance

    Returns:
        CachedRecordsResponse
    """
    cache_data = await backend.get_cache_data()

    now = time.time()
    cached_records: list[CachedRecord] = []

    for cache_key, (etag_content, expiry) in cache_data.items():
        method, host, path, query_params = _parse_cache_key(cache_key)
        if method:  # Valid cache key
            # Check if cache entry is expired
            is_expired = expiry is not None and expiry <= now

            # Get content size
            content = etag_content.content
            content_size = len(content) if isinstance(content, (bytes, str)) else 0

            ttl_remaining = round(expiry - now, 2) if expiry is not None else None

            content_preview = (
                content[:100].decode("utf-8", errors="ignore")
                if isinstance(content, bytes)
                else str(content)[:100]
            )

            cached_records.append(
                CachedRecord(
                    cache_key=cache_key,
                    method=method,
                    host=host,
                    path=path,
                    query_params=query_params,
                    etag=etag_content.etag,
                    content_type=type(content).__name__,
                    content_size=content_size,
                    is_expired=is_expired,
                    ttl_remaining=ttl_remaining,
                    content_preview=content_preview,
                )
            )

    # Count active records and total size
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
    app: "FastAPI", prefix: str = "", include_in_schema: bool = False
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

    Example:
        from fastapi import FastAPI
        from fastapi_cachex import add_routes

        app = FastAPI()
        add_routes(app)  # Routes at /cached-hits and /cached-records

        # Or with prefix
        add_routes(app, prefix="/api/cache")  # Routes at /api/cache/cached-hits and /api/cache/cached-records
    """

    @app.get(f"{prefix}/cached-hits", include_in_schema=include_in_schema)
    async def get_cached_hits() -> CacheHitsResponse:
        """Return cached hit records.

        Shows cache statistics including which routes are frequently being cached,
        hit counts, and cache key information.

        Returns:
            CacheHitsResponse containing cache hit records and statistics
        """
        try:
            backend: BaseCacheBackend = BackendProxy.get()
        except BackendNotFoundError:
            return CacheHitsResponse(
                cached_hits=[],
                total_hits=0,
                valid_hits=0,
                expired_hits=0,
                unique_routes=0,
                summary=CacheHitSummary(
                    total_cached_entries=0,
                    active_entries=0,
                    frequently_cached_routes=[],
                ),
            )

        return await _get_cached_hits_handler(backend)

    @app.get(f"{prefix}/cached-records", include_in_schema=include_in_schema)
    async def get_cached_records() -> CachedRecordsResponse:
        """Display currently cached records.

        Returns all currently cached records in the cache backend with their
        content information and expiry details.

        Returns:
            CachedRecordsResponse containing cached records and statistics
        """
        try:
            backend: BaseCacheBackend = BackendProxy.get()
        except BackendNotFoundError:
            return CachedRecordsResponse(
                cached_records=[],
                total_records=0,
                active_records=0,
                expired_records=0,
                total_cache_size_bytes=0,
                summary=CacheSummary(
                    total_entries=0,
                    valid_entries=0,
                    estimated_cache_size_kb=0.0,
                ),
            )

        return await _get_cached_records_handler(backend)
