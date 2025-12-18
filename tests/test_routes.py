"""Tests for cache monitoring routes."""

import pytest
from fastapi import FastAPI
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex import add_routes
from fastapi_cachex import cache
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.proxy import BackendProxy


@pytest.fixture
def app():
    """Create a test FastAPI application."""
    return FastAPI()


@pytest.fixture
def client(app):
    """Create a test client for the FastAPI application."""
    return TestClient(app)


@pytest.fixture
def setup_cache():
    """Setup cache backend before test and cleanup after."""
    backend = MemoryBackend()
    BackendProxy.set_backend(backend)
    backend.start_cleanup()
    yield backend
    backend.stop_cleanup()
    BackendProxy.set_backend(None)


class TestCachedHitsRoute:
    """Test suite for the /cached-hits route."""

    def test_cached_hits_without_backend(self, app, client):
        """Test /cached-hits returns empty when backend not configured."""
        add_routes(app)

        response = client.get("/cached-hits")
        assert response.status_code == 200
        data = response.json()
        assert data["cached_hits"] == []
        assert data["total_hits"] == 0
        assert data["valid_hits"] == 0
        assert data["expired_hits"] == 0

    def test_cached_hits_empty_cache(self, app, client, setup_cache):
        """Test /cached-hits returns empty structure when cache is empty."""
        add_routes(app)

        response = client.get("/cached-hits")
        assert response.status_code == 200
        data = response.json()
        assert data["cached_hits"] == []
        assert data["total_hits"] == 0
        assert data["valid_hits"] == 0
        assert data["unique_routes"] == 0

    def test_cached_hits_with_entries(self, app, client, setup_cache):
        """Test /cached-hits returns cached entries when routes are cached."""
        add_routes(app)

        @app.get("/api/users")
        @cache(ttl=60)
        async def get_users():
            return Response(
                content=b'[{"id": 1, "name": "Alice"}]',
                media_type="application/json",
            )

        @app.get("/api/products")
        @cache(ttl=60)
        async def get_products():
            return Response(
                content=b'[{"id": 1, "name": "Product A"}]',
                media_type="application/json",
            )

        # Make requests to populate cache
        client.get("/api/users")
        client.get("/api/products")

        # Get cache hits information
        response = client.get("/cached-hits")
        assert response.status_code == 200
        data = response.json()

        assert data["total_hits"] == 2
        assert data["valid_hits"] == 2
        assert data["expired_hits"] == 0
        assert data["unique_routes"] == 2
        assert len(data["cached_hits"]) == 2

        # Check summary
        assert data["summary"]["total_cached_entries"] == 2
        assert data["summary"]["active_entries"] == 2
        assert set(data["summary"]["frequently_cached_routes"]) == {
            "/api/users",
            "/api/products",
        }

    def test_cached_hits_route_structure(self, app, client, setup_cache):
        """Test that cached hit records have correct structure."""
        add_routes(app)

        @app.get("/api/test")
        @cache(ttl=60)
        async def test_endpoint():
            return {"data": "test"}

        client.get("/api/test?query=value")

        response = client.get("/cached-hits")
        assert response.status_code == 200
        data = response.json()

        assert len(data["cached_hits"]) == 1
        hit = data["cached_hits"][0]

        # Verify structure
        assert "cache_key" in hit
        assert "method" in hit
        assert "host" in hit
        assert "path" in hit
        assert "query_params" in hit
        assert "etag" in hit
        assert "is_expired" in hit
        assert "ttl_remaining" in hit

        # Verify values
        assert hit["method"] == "GET"
        assert hit["path"] == "/api/test"
        assert hit["query_params"] == "query=value"
        assert hit["is_expired"] is False
        assert hit["ttl_remaining"] is not None
        assert isinstance(hit["ttl_remaining"], float)

    def test_cached_hits_with_prefix(self, app, client, setup_cache):
        """Test /cached-hits route with custom prefix."""
        add_routes(app, prefix="/admin/cache")

        @app.get("/test")
        @cache(ttl=60)
        async def test_endpoint():
            return {"data": "test"}

        client.get("/test")

        response = client.get("/admin/cache/cached-hits")
        assert response.status_code == 200
        data = response.json()
        assert data["total_hits"] == 1

    def test_cached_hits_multiple_query_variations(self, app, client, setup_cache):
        """Test /cached-hits shows different cache keys for query params."""
        add_routes(app)

        @app.get("/api/items")  # type: ignore[untyped-decorator]
        @cache(ttl=60)
        async def get_items(item_id: int):
            return {"id": item_id}

        # Make requests with different query params
        client.get("/api/items?item_id=1")
        client.get("/api/items?item_id=2")

        response = client.get("/cached-hits")
        assert response.status_code == 200
        data = response.json()

        assert data["total_hits"] == 2
        assert data["unique_routes"] == 1  # Same path
        assert len(data["cached_hits"]) == 2

        # Check that query params are different
        query_params = {hit["query_params"] for hit in data["cached_hits"]}
        assert "item_id=1" in query_params
        assert "item_id=2" in query_params


class TestCachedRecordsRoute:
    """Test suite for the /cached-records route."""

    def test_cached_records_without_backend(self, app, client):
        """Test /cached-records returns empty when backend not configured."""
        add_routes(app)

        response = client.get("/cached-records")
        assert response.status_code == 200
        data = response.json()
        assert data["cached_records"] == []
        assert data["total_records"] == 0
        assert data["active_records"] == 0

    def test_cached_records_empty_cache(self, app, client, setup_cache):
        """Test /cached-records returns empty structure when cache is empty."""
        add_routes(app)

        response = client.get("/cached-records")
        assert response.status_code == 200
        data = response.json()
        assert data["cached_records"] == []
        assert data["total_records"] == 0
        assert data["active_records"] == 0
        assert data["total_cache_size_bytes"] == 0

    def test_cached_records_with_entries(self, app, client, setup_cache):
        """Test /cached-records returns cached entries with content info."""
        add_routes(app)

        @app.get("/api/users")
        @cache(ttl=60)
        async def get_users():
            return Response(
                content=b'[{"id": 1, "name": "Alice"}]',
                media_type="application/json",
            )

        @app.get("/api/products")
        @cache(ttl=60)
        async def get_products():
            return Response(
                content=b'[{"id": 1, "name": "Product A"}]',
                media_type="application/json",
            )

        # Make requests to populate cache
        client.get("/api/users")
        client.get("/api/products")

        response = client.get("/cached-records")
        assert response.status_code == 200
        data = response.json()

        assert data["total_records"] == 2
        assert data["active_records"] == 2
        assert data["expired_records"] == 0
        assert data["total_cache_size_bytes"] > 0

    def test_cached_records_structure(self, app, client, setup_cache):
        """Test that cached records have correct structure."""
        add_routes(app)

        @app.get("/api/test")
        @cache(ttl=60)
        async def test_endpoint():
            return {"data": "test"}

        client.get("/api/test")

        response = client.get("/cached-records")
        assert response.status_code == 200
        data = response.json()

        assert len(data["cached_records"]) == 1
        record = data["cached_records"][0]

        # Verify structure
        assert "cache_key" in record
        assert "method" in record
        assert "host" in record
        assert "path" in record
        assert "query_params" in record
        assert "etag" in record
        assert "content_type" in record
        assert "content_size" in record
        assert "is_expired" in record
        assert "ttl_remaining" in record
        assert "content_preview" in record

        # Verify values
        assert record["method"] == "GET"
        assert record["path"] == "/api/test"
        assert record["is_expired"] is False
        assert record["content_size"] > 0
        assert record["content_type"] in ("bytes", "str")

    def test_cached_records_content_size_calculation(self, app, client, setup_cache):
        """Test that content size is calculated correctly."""
        add_routes(app)

        @app.get("/api/small")
        @cache(ttl=60)
        async def small_endpoint():
            return Response(
                content=b"small",
                media_type="text/plain",
            )

        @app.get("/api/large")
        @cache(ttl=60)
        async def large_endpoint():
            return Response(
                content=b"x" * 1000,
                media_type="text/plain",
            )

        client.get("/api/small")
        client.get("/api/large")

        response = client.get("/cached-records")
        assert response.status_code == 200
        data = response.json()

        records = {r["path"]: r for r in data["cached_records"]}
        assert records["/api/small"]["content_size"] == 5
        assert records["/api/large"]["content_size"] == 1000

    def test_cached_records_with_prefix(self, app, client, setup_cache):
        """Test /cached-records route with custom prefix."""
        add_routes(app, prefix="/api/cache")

        @app.get("/test")
        @cache(ttl=60)
        async def test_endpoint():
            return {"data": "test"}

        client.get("/test")

        response = client.get("/api/cache/cached-records")
        assert response.status_code == 200
        data = response.json()
        assert data["total_records"] == 1

    def test_cached_records_content_preview(self, app, client, setup_cache):
        """Test that content preview is limited to 100 bytes."""
        add_routes(app)

        @app.get("/api/large")
        @cache(ttl=60)
        async def large_endpoint():
            return Response(
                content=b"x" * 500,
                media_type="text/plain",
            )

        client.get("/api/large")

        response = client.get("/cached-records")
        assert response.status_code == 200
        data = response.json()

        record = data["cached_records"][0]
        assert len(record["content_preview"]) == 100

    def test_cached_records_summary_calculations(self, app, client, setup_cache):
        """Test that summary calculations are correct."""
        add_routes(app)

        @app.get("/api/test1")
        @cache(ttl=60)
        async def test1():
            return Response(content=b"a" * 500)

        @app.get("/api/test2")
        @cache(ttl=60)
        async def test2():
            return Response(content=b"b" * 300)

        client.get("/api/test1")
        client.get("/api/test2")

        response = client.get("/cached-records")
        assert response.status_code == 200
        data = response.json()

        summary = data["summary"]
        assert summary["total_entries"] == 2
        assert summary["valid_entries"] == 2
        # Should be approximately 0.78 KB (800 / 1024)
        assert 0.75 < summary["estimated_cache_size_kb"] < 0.85


class TestRoutesIntegration:
    """Integration tests for monitoring routes."""

    def test_routes_without_prefix(self, app, client, setup_cache):
        """Test that routes work without prefix."""
        add_routes(app)

        @app.get("/test")
        @cache(ttl=60)
        async def test_endpoint():
            return {"data": "test"}

        client.get("/test")

        hits_response = client.get("/cached-hits")
        records_response = client.get("/cached-records")

        assert hits_response.status_code == 200
        assert records_response.status_code == 200

    def test_routes_consistency(self, app, client, setup_cache):
        """Test that both routes show consistent data."""
        add_routes(app)

        @app.get("/api/consistent")
        @cache(ttl=60)
        async def consistent_endpoint():
            return Response(content=b"test data")

        client.get("/api/consistent")

        hits_response = client.get("/cached-hits")
        records_response = client.get("/cached-records")

        hits_data = hits_response.json()
        records_data = records_response.json()

        # Both should show 1 cache entry
        assert hits_data["total_hits"] == 1
        assert records_data["total_records"] == 1

        # Same path should be in both
        hit_path = hits_data["cached_hits"][0]["path"]
        record_path = records_data["cached_records"][0]["path"]
        assert hit_path == record_path == "/api/consistent"

    def test_routes_not_cached_by_default(self, app, client, setup_cache):
        """Test that the monitoring routes themselves are not cached."""
        add_routes(app)

        @app.get("/api/test")
        @cache(ttl=60)
        async def test_endpoint():
            return {"data": "test"}

        client.get("/api/test")
        client.get("/cached-hits")
        client.get("/cached-records")

        # Only the /api/test should be cached
        hits_response = client.get("/cached-hits")
        hits_data = hits_response.json()
        assert hits_data["total_hits"] == 1
        assert hits_data["cached_hits"][0]["path"] == "/api/test"

    def test_include_in_schema_parameter(self, app):
        """Test that include_in_schema parameter works."""
        add_routes(app, include_in_schema=True)

        # Check if routes are included in OpenAPI schema
        openapi_schema = app.openapi()
        assert openapi_schema is not None
        paths = openapi_schema.get("paths", {})

        # Routes should be present in the schema
        assert "/cached-hits" in paths
        assert "/cached-records" in paths
