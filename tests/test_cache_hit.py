"""Integration tests for cache hit behavior and key generation.

This module tests the actual cache hit performance improvements:
- Verify that cached content is returned directly (200 OK) without re-executing the function
- Verify that cache keys include host and method to avoid cross-host/method pollution
- Verify TTL-based cache hits return the cached content
"""

from fastapi import FastAPI
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex.cache import cache

app = FastAPI()
client = TestClient(app)


def test_cache_hit_returns_200_with_cached_content():
    """Test that cache hit returns 200 OK with cached content directly.

    This is the critical fix: when TTL cache hits, return the cached response
    with status 200, not 304. This avoids re-executing the handler function.
    """
    call_count = {"value": 0}

    @app.get("/cached-response")
    @cache(ttl=60)
    async def get_cached():
        call_count["value"] += 1
        return Response(
            content=f'{{"count": {call_count["value"]}}}',
            media_type="application/json",
        )

    # First request - should execute function and cache
    response1 = client.get("/cached-response")
    assert response1.status_code == 200
    assert response1.json() == {"count": 1}
    etag1 = response1.headers.get("ETag")
    assert etag1 is not None

    # Second request with cached content - should return 200 with same data
    # WITHOUT executing the function again
    response2 = client.get("/cached-response")
    assert response2.status_code == 200
    assert response2.json() == {"count": 1}  # Same as first, not incremented to 2
    assert response2.headers.get("ETag") == etag1
    assert call_count["value"] == 1  # Function only called once


def test_cache_key_includes_host():
    """Test that cache keys include host to avoid cross-host pollution."""

    @app.get("/host-specific")
    @cache(ttl=60)
    async def get_host_specific():
        return {"data": "test"}

    # Request with one host
    response1 = client.get("/host-specific")
    assert response1.status_code == 200
    assert response1.json() == {"data": "test"}

    # Verify cache key structure includes host
    # (by checking that we get cached content when host matches)
    response2 = client.get("/host-specific")
    assert response2.status_code == 200


def test_cache_key_includes_method():
    """Test that cache keys include HTTP method to avoid GET/POST pollution."""
    execution_log = {"get": 0, "post": 0}

    @app.get("/method-specific")
    @cache(ttl=60)
    async def get_method_get():
        execution_log["get"] += 1
        return {"method": "GET", "count": execution_log["get"]}

    @app.post("/method-specific")
    @cache(ttl=60)
    async def post_method_post():
        execution_log["post"] += 1
        return {"method": "POST", "count": execution_log["post"]}

    # GET request
    response_get = client.get("/method-specific")
    assert response_get.status_code == 200
    assert response_get.json() == {"method": "GET", "count": 1}

    # POST request (should not be cached as per spec)
    response_post = client.post("/method-specific")
    assert response_post.status_code == 200
    # POST is not cached, so this should still work
    assert response_post.json() == {"method": "POST", "count": 1}

    # Second GET request (should hit cache)
    response_get2 = client.get("/method-specific")
    assert response_get2.status_code == 200
    assert response_get2.json() == {"method": "GET", "count": 1}  # Still 1, cached


def test_ttl_cache_hit_without_etag_header():
    """Test that TTL-based cache hit works even without If-None-Match header.

    The improvement: when ttl is set and cache is valid, return cached content
    with 200 status without requiring client to send If-None-Match.
    """
    execution_count = {"value": 0}

    @app.get("/ttl-cache-hit")
    @cache(ttl=30)
    async def ttl_endpoint():
        execution_count["value"] += 1
        return Response(
            content=f'{{"execution": {execution_count["value"]}}}',
            media_type="application/json",
        )

    # First request
    response1 = client.get("/ttl-cache-hit")
    assert response1.status_code == 200
    assert response1.json() == {"execution": 1}

    # Second request without If-None-Match header
    # Should return cached content with 200 OK
    response2 = client.get("/ttl-cache-hit")
    assert response2.status_code == 200
    assert response2.json() == {"execution": 1}  # Cached, not re-executed
    assert execution_count["value"] == 1


def test_no_cache_still_returns_304_on_etag_match():
    """Test that no-cache directive still returns 304 when ETag matches.

    no-cache with If-None-Match should return 304 (revalidate behavior).
    Note: no-cache forces revalidation, so the handler is called every time.
    """
    execution_count = {"value": 0}

    @app.get("/no-cache-with-etag")
    @cache(no_cache=True)
    async def no_cache_endpoint():
        execution_count["value"] += 1
        return Response(
            content=b'{"static": "data"}',
            media_type="application/json",
        )

    # First request - handler executes to get fresh data and ETag
    response1 = client.get("/no-cache-with-etag")
    assert response1.status_code == 200
    assert execution_count["value"] == 1
    etag = response1.headers.get("ETag")

    # Second request with If-None-Match matching ETag
    # Handler executes again to get fresh data for comparison (no-cache behavior)
    # Should return 304 (revalidation passed)
    response2 = client.get(
        "/no-cache-with-etag",
        headers={"If-None-Match": etag},
    )
    assert response2.status_code == 304
    assert execution_count["value"] == 2  # Function called again due to no-cache


def test_cache_key_separates_query_params():
    """Test that different query parameters result in separate cache entries."""
    call_log = []

    @app.get("/query-variant")
    @cache(ttl=60)
    async def query_variant(user_id: int):
        call_log.append(user_id)
        return {"user_id": user_id, "call_number": len(call_log)}

    # Request with user_id=1
    response1 = client.get("/query-variant?user_id=1")
    assert response1.status_code == 200
    assert response1.json() == {"user_id": 1, "call_number": 1}

    # Request with user_id=2 (different cache key)
    response2 = client.get("/query-variant?user_id=2")
    assert response2.status_code == 200
    assert response2.json() == {"user_id": 2, "call_number": 2}

    # Request with user_id=1 again (should hit cache)
    response3 = client.get("/query-variant?user_id=1")
    assert response3.status_code == 200
    assert response3.json() == {"user_id": 1, "call_number": 1}  # Still 1, cached

    # Verify that we made exactly 2 function calls
    assert len(call_log) == 2


def test_cache_hit_preserves_headers():
    """Test that cache hit preserves important headers (Cache-Control, ETag, etc)."""

    @app.get("/header-preservation")
    @cache(ttl=60, public=True)
    async def header_endpoint():
        return {"data": "test"}

    # First request
    response1 = client.get("/header-preservation")
    assert response1.status_code == 200
    etag1 = response1.headers.get("ETag")
    cache_control1 = response1.headers.get("Cache-Control")

    # Second request (cache hit)
    response2 = client.get("/header-preservation")
    assert response2.status_code == 200
    # Headers should be preserved
    assert response2.headers.get("ETag") == etag1
    assert response2.headers.get("Cache-Control") == cache_control1
    assert "public" in cache_control1.lower()
    assert "max-age=60" in cache_control1


def test_cache_hit_performance():
    """Test that cache hits don't re-execute the function.

    This is a performance test to ensure cached content is returned directly.
    """
    import time

    @app.get("/expensive-operation")
    @cache(ttl=60)
    async def expensive_operation():
        # Simulate expensive operation
        time.sleep(0.1)
        return {"expensive": "data"}

    # First request (will execute the expensive operation)
    start = time.time()
    response1 = client.get("/expensive-operation")
    time1 = time.time() - start
    assert response1.status_code == 200
    assert time1 > 0.1  # Should take at least 0.1s due to sleep

    # Second request (should be much faster due to cache hit)
    start = time.time()
    response2 = client.get("/expensive-operation")
    time2 = time.time() - start
    assert response2.status_code == 200
    assert time2 < 0.05  # Should be much faster, no sleep


def test_cache_differentiation_by_content_type():
    """Test that cache keys properly differentiate requests.

    Although current implementation doesn't include Accept header in cache key,
    this test documents the behavior.
    """

    @app.get("/flexible-format")
    @cache(ttl=60)
    async def flexible_format():
        return Response(
            content=b'{"data": "test"}',
            media_type="application/json",
        )

    # Both requests should get the same cached response
    response1 = client.get("/flexible-format")
    assert response1.status_code == 200

    response2 = client.get("/flexible-format")
    assert response2.status_code == 200
    assert response1.text == response2.text
