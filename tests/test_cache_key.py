"""Tests for cache key generation and parsing."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.cache import cache
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.routes import _parse_cache_key
from fastapi_cachex.types import CACHE_KEY_SEPARATOR


class TestCacheKeyGeneration:
    """Test cache key generation with various host formats."""

    def test_cache_key_with_host_and_port(self):
        """Test cache key generation with host containing port number (e.g., 127.0.0.1:8000)."""
        app = FastAPI()
        backend = MemoryBackend()
        BackendProxy.set_backend(backend)

        @app.get("/api/test")
        @cache(ttl=60)
        async def test_endpoint():
            return {"data": "test"}

        client = TestClient(app, base_url="http://127.0.0.1:8000")
        response = client.get("/api/test")

        assert response.status_code == 200

        # Verify cache key was generated correctly
        cache_keys = list(backend.cache.keys())
        assert len(cache_keys) == 1

        cache_key = cache_keys[0]
        # Should contain the separator, not be split incorrectly by colons
        assert CACHE_KEY_SEPARATOR in cache_key

        # Parse the cache key to verify components
        method, host, path, query_params = _parse_cache_key(cache_key)

        assert method == "GET"
        assert host == "127.0.0.1:8000"  # Port should be part of host
        assert path == "/api/test"
        assert query_params == ""

    def test_cache_key_with_localhost(self):
        """Test cache key generation with localhost."""
        app = FastAPI()
        backend = MemoryBackend()
        BackendProxy.set_backend(backend)

        @app.get("/api/users")
        @cache(ttl=60)
        async def users_endpoint():
            return {"users": []}

        client = TestClient(app, base_url="http://localhost:8080")
        response = client.get("/api/users")

        assert response.status_code == 200

        cache_keys = list(backend.cache.keys())
        assert len(cache_keys) == 1

        method, host, path, query_params = _parse_cache_key(cache_keys[0])

        assert method == "GET"
        assert host == "localhost:8080"
        assert path == "/api/users"
        assert query_params == ""

    def test_cache_key_with_query_params(self):
        """Test cache key generation with query parameters."""
        app = FastAPI()
        backend = MemoryBackend()
        BackendProxy.set_backend(backend)

        @app.get("/api/search")
        @cache(ttl=60)
        async def search_endpoint(q: str = ""):
            return {"query": q}

        client = TestClient(app, base_url="http://127.0.0.1:8000")
        response = client.get("/api/search?q=test")

        assert response.status_code == 200

        cache_keys = list(backend.cache.keys())
        assert len(cache_keys) == 1

        method, host, path, query_params = _parse_cache_key(cache_keys[0])

        assert method == "GET"
        assert host == "127.0.0.1:8000"
        assert path == "/api/search"
        assert query_params == "q=test"

    def test_cache_key_with_ipv6_address(self):
        """Test cache key parsing with IPv6 address containing colons."""
        # IPv6 addresses contain multiple colons, test that our separator doesn't break this
        cache_key = f"GET{CACHE_KEY_SEPARATOR}[::1]:8000{CACHE_KEY_SEPARATOR}/api/data{CACHE_KEY_SEPARATOR}"
        method, host, path, query_params = _parse_cache_key(cache_key)

        assert method == "GET"
        assert host == "[::1]:8000"
        assert path == "/api/data"
        assert query_params == ""


class TestCacheKeyParsing:
    """Test cache key parsing function."""

    def test_parse_valid_cache_key(self):
        """Test parsing a valid cache key."""
        cache_key = f"GET{CACHE_KEY_SEPARATOR}localhost:8000{CACHE_KEY_SEPARATOR}/api/test{CACHE_KEY_SEPARATOR}id=123"
        method, host, path, query_params = _parse_cache_key(cache_key)

        assert method == "GET"
        assert host == "localhost:8000"
        assert path == "/api/test"
        assert query_params == "id=123"

    def test_parse_cache_key_without_query_params(self):
        """Test parsing cache key without query parameters."""
        cache_key = f"POST{CACHE_KEY_SEPARATOR}127.0.0.1:3000{CACHE_KEY_SEPARATOR}/api/create{CACHE_KEY_SEPARATOR}"
        method, host, path, query_params = _parse_cache_key(cache_key)

        assert method == "POST"
        assert host == "127.0.0.1:3000"
        assert path == "/api/create"
        assert query_params == ""

    def test_parse_cache_key_with_complex_host(self):
        """Test parsing cache key with complex host (subdomain + port)."""
        cache_key = f"GET{CACHE_KEY_SEPARATOR}api.example.com:443{CACHE_KEY_SEPARATOR}/v1/users{CACHE_KEY_SEPARATOR}limit=10"
        method, host, path, query_params = _parse_cache_key(cache_key)

        assert method == "GET"
        assert host == "api.example.com:443"
        assert path == "/v1/users"
        assert query_params == "limit=10"

    def test_parse_invalid_cache_key(self):
        """Test parsing invalid cache key returns empty strings."""
        cache_key = "invalid_key"
        method, host, path, query_params = _parse_cache_key(cache_key)

        assert method == ""
        assert host == ""
        assert path == ""
        assert query_params == ""

    def test_cache_key_separator_constant(self):
        """Test that CACHE_KEY_SEPARATOR constant is correctly defined."""
        assert CACHE_KEY_SEPARATOR == "|||"
        # Verify it doesn't conflict with common URL characters
        assert ":" not in CACHE_KEY_SEPARATOR
        assert "/" not in CACHE_KEY_SEPARATOR
        assert "?" not in CACHE_KEY_SEPARATOR
        assert "&" not in CACHE_KEY_SEPARATOR


class TestCacheKeyDifferentiation:
    """Test that different requests generate different cache keys."""

    def test_different_hosts_generate_different_keys(self):
        """Test that requests to different hosts generate different cache keys."""
        app = FastAPI()
        backend = MemoryBackend()
        BackendProxy.set_backend(backend)

        @app.get("/api/test")
        @cache(ttl=60)
        async def test_endpoint():
            return {"data": "test"}

        # First request with one host
        client1 = TestClient(app, base_url="http://localhost:8000")
        client1.get("/api/test")

        # Second request with different host
        client2 = TestClient(app, base_url="http://127.0.0.1:8000")
        client2.get("/api/test")

        # Should have 2 different cache entries
        cache_keys = list(backend.cache.keys())
        assert len(cache_keys) == 2

        # Parse both keys and verify they differ in host
        key1_method, key1_host, key1_path, _ = _parse_cache_key(cache_keys[0])
        key2_method, key2_host, key2_path, _ = _parse_cache_key(cache_keys[1])

        assert key1_host != key2_host
        assert key1_method == key2_method == "GET"
        assert key1_path == key2_path == "/api/test"

    def test_different_ports_generate_different_keys(self):
        """Test that same host with different ports generate different cache keys."""
        app = FastAPI()
        backend = MemoryBackend()
        BackendProxy.set_backend(backend)

        @app.get("/api/data")
        @cache(ttl=60)
        async def data_endpoint():
            return {"data": "test"}

        # First request with port 8000
        client1 = TestClient(app, base_url="http://localhost:8000")
        client1.get("/api/data")

        # Second request with port 9000
        client2 = TestClient(app, base_url="http://localhost:9000")
        client2.get("/api/data")

        # Should have 2 different cache entries
        cache_keys = list(backend.cache.keys())
        assert len(cache_keys) == 2

        # Verify hosts are different
        key1_method, key1_host, key1_path, _ = _parse_cache_key(cache_keys[0])
        key2_method, key2_host, key2_path, _ = _parse_cache_key(cache_keys[1])

        assert key1_host == "localhost:8000"
        assert key2_host == "localhost:9000"

        assert key1_method == key2_method == "GET"
        assert key1_path == key2_path == "/api/data"
