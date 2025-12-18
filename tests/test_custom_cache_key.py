"""Tests for custom cache key builder functionality."""

from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import cache
from fastapi_cachex import default_key_builder
from fastapi_cachex.backends import MemoryBackend


def test_default_cache_key_builder() -> None:
    """Test that default cache key builder works as expected."""
    app = FastAPI()
    backend = MemoryBackend()
    BackendProxy.set_backend(backend)

    @app.get("/items")
    @cache(ttl=60)
    async def get_items():
        return {"message": "Hello, World!"}

    client = TestClient(app)

    # First request - cache miss
    response1 = client.get("/items?page=1")
    assert response1.status_code == 200
    assert response1.json() == {"message": "Hello, World!"}

    # Second request - cache hit (should have same response)
    response2 = client.get("/items?page=1")
    assert response2.status_code == 200
    assert response2.json() == {"message": "Hello, World!"}

    # Different query params - cache miss (new cache entry)
    response3 = client.get("/items?page=2")
    assert response3.status_code == 200
    assert response3.json() == {"message": "Hello, World!"}


def test_custom_cache_key_builder_ignore_query_params() -> None:
    """Test custom cache key builder that ignores query parameters."""
    app = FastAPI()
    backend = MemoryBackend()
    BackendProxy.set_backend(backend)

    def custom_key_builder(request: Request) -> str:
        """Build cache key without query parameters."""
        return f"{request.method}||{request.url.path}"

    @app.get("/products")
    @cache(ttl=60, cache_key_builder=custom_key_builder)
    async def get_products():
        return {"message": "Products"}

    client = TestClient(app)

    # First request with query params
    response1 = client.get("/products?page=1&sort=asc")
    assert response1.status_code == 200
    etag1 = response1.headers.get("etag")

    # Second request with different query params - should still hit cache
    response2 = client.get("/products?page=2&sort=desc")
    assert response2.status_code == 200
    etag2 = response2.headers.get("etag")

    # ETags should be identical because cache key ignores query params
    assert etag1 == etag2


def test_custom_cache_key_builder_with_user_id() -> None:
    """Test custom cache key builder that includes user-specific data."""
    app = FastAPI()
    backend = MemoryBackend()
    BackendProxy.set_backend(backend)

    def user_specific_key_builder(request: Request) -> str:
        """Build cache key with user ID from headers."""
        user_id = request.headers.get("X-User-ID", "anonymous")
        return f"{request.method}||{request.url.path}||{user_id}"

    @app.get("/profile")
    @cache(ttl=60, cache_key_builder=user_specific_key_builder)
    async def get_profile(request: Request):
        user_id = request.headers.get("X-User-ID", "anonymous")
        return {"user": user_id, "message": "Profile data"}

    client = TestClient(app)

    # Request for user1
    response1 = client.get("/profile", headers={"X-User-ID": "user1"})
    assert response1.status_code == 200
    assert response1.json() == {"user": "user1", "message": "Profile data"}

    # Request for user2 - different cache entry
    response2 = client.get("/profile", headers={"X-User-ID": "user2"})
    assert response2.status_code == 200
    assert response2.json() == {"user": "user2", "message": "Profile data"}

    # Request for user1 again - cache hit
    response3 = client.get("/profile", headers={"X-User-ID": "user1"})
    assert response3.status_code == 200
    assert response3.json() == {"user": "user1", "message": "Profile data"}


def test_custom_cache_key_builder_with_language() -> None:
    """Test custom cache key builder that includes language preference."""
    app = FastAPI()
    backend = MemoryBackend()
    BackendProxy.set_backend(backend)

    def language_aware_key_builder(request: Request) -> str:
        """Build cache key with language from Accept-Language header."""
        lang = request.headers.get("Accept-Language", "en")
        return f"{request.method}||{request.url.path}||{lang}"

    @app.get("/content")
    @cache(ttl=60, cache_key_builder=language_aware_key_builder)
    async def get_content(request: Request):
        lang = request.headers.get("Accept-Language", "en")
        messages = {
            "en": "Hello",
            "zh-TW": "你好",
            "ja": "こんにちは",
        }
        return {"message": messages.get(lang, "Hello")}

    client = TestClient(app)

    # English request
    response_en = client.get("/content", headers={"Accept-Language": "en"})
    assert response_en.status_code == 200
    assert response_en.json() == {"message": "Hello"}

    # Chinese request - different cache entry
    response_zh = client.get("/content", headers={"Accept-Language": "zh-TW"})
    assert response_zh.status_code == 200
    assert response_zh.json() == {"message": "你好"}

    # Japanese request - different cache entry
    response_ja = client.get("/content", headers={"Accept-Language": "ja"})
    assert response_ja.status_code == 200
    assert response_ja.json() == {"message": "こんにちは"}


def test_cache_key_builder_none_uses_default() -> None:
    """Test that passing None for cache_key_builder uses the default."""
    app = FastAPI()
    backend = MemoryBackend()
    BackendProxy.set_backend(backend)

    @app.get("/default")
    @cache(ttl=60, cache_key_builder=None)
    async def get_default():
        return {"message": "Using default"}

    client = TestClient(app)

    response = client.get("/default")
    assert response.status_code == 200
    assert response.json() == {"message": "Using default"}


def test_default_key_builder_function() -> None:
    """Test the default_key_builder function directly."""
    from unittest.mock import MagicMock

    # Create a mock request
    mock_request = MagicMock(spec=Request)
    mock_request.method = "GET"
    mock_request.headers = {"host": "example.com"}
    mock_request.url.path = "/api/items"
    mock_request.query_params = "page=1&limit=10"

    # Generate cache key
    cache_key = default_key_builder(mock_request)

    # Verify format: method|||host|||path|||query_params
    expected = "GET|||example.com|||/api/items|||page=1&limit=10"
    assert cache_key == expected


def test_default_key_builder_without_host() -> None:
    """Test default_key_builder when host header is missing."""
    from unittest.mock import MagicMock

    # Create a mock request without host
    mock_request = MagicMock(spec=Request)
    mock_request.method = "GET"
    mock_request.headers = {}
    mock_request.url.path = "/api/items"
    mock_request.query_params = ""

    # Generate cache key
    cache_key = default_key_builder(mock_request)

    # Should use 'unknown' as fallback for host
    expected = "GET|||unknown|||/api/items|||"
    assert cache_key == expected
