"""Tests for replaying a cached response's status code and headers.

Only successful responses may be stored: an error a handler *returns* (as
opposed to raising) used to be cached and then replayed as ``200 OK``. A cache
hit also used to drop every header the handler set, so a response changed shape
depending on whether it came from cache.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.cache import cache
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

from .backends.test_memcached import MEMCACHED_SERVER
from .backends.test_memcached import requires_memcached
from .backends.test_redis import REDIS_HOST
from .backends.test_redis import REDIS_PORT
from .backends.test_redis import requires_redis


def _key(path: str) -> str:
    """The key `default_key_builder` produces for a TestClient GET."""
    return f"GET|||testserver|||{path}|||"


def test_returned_error_is_not_cached_and_keeps_its_status():
    """A 404 the handler returns must never be stored nor replayed as 200."""
    app = FastAPI()
    client = TestClient(app)
    calls = {"n": 0}

    @app.get("/missing")
    @cache(ttl=60)
    async def missing():
        calls["n"] += 1
        return Response(content='{"error": "not found"}', status_code=404)

    for _ in range(3):
        response = client.get("/missing")
        assert response.status_code == 404
        assert response.json() == {"error": "not found"}

    # The handler ran every time, and nothing was written to the backend.
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_returned_error_leaves_no_backend_entry():
    """The failing key must be absent from the backend entirely."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/boom")
    @cache(ttl=60)
    async def boom():
        return Response(content="kaboom", status_code=500)

    assert client.get("/boom").status_code == 500
    assert await BackendProxy.get().get(_key("/boom")) is None


@pytest.mark.asyncio
async def test_error_does_not_overwrite_a_good_cached_entry():
    """A transient failure must not evict the last good response.

    Uses ETag-only mode (no ``ttl``) so the handler runs on every request; with
    a live TTL the cached copy would be served without calling it at all.
    """
    app = FastAPI()
    client = TestClient(app)
    state = {"fail": False}

    @app.get("/flaky")
    @cache()
    async def flaky():
        if state["fail"]:
            return Response(content="down", status_code=503)
        return Response(content="good", media_type="text/plain")

    assert client.get("/flaky").text == "good"
    good = await BackendProxy.get().get(_key("/flaky"))
    assert good is not None
    assert good.content == b"good"

    state["fail"] = True
    assert client.get("/flaky").status_code == 503

    # The stored entry is byte-for-byte the one from before the failure.
    assert await BackendProxy.get().get(_key("/flaky")) == good

    state["fail"] = False
    assert client.get("/flaky").text == "good"


def test_cache_hit_restores_custom_headers_and_2xx_status():
    """Custom headers and a non-200 success status survive a cache hit."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/report")
    @cache(ttl=60)
    async def report():
        return Response(
            content="body",
            status_code=203,
            media_type="text/plain",
            headers={"X-Total-Count": "42", "Content-Language": "en"},
        )

    miss = client.get("/report")
    hit = client.get("/report")

    for response in (miss, hit):
        assert response.status_code == 203
        assert response.headers["X-Total-Count"] == "42"
        assert response.headers["Content-Language"] == "en"
        assert response.headers["content-type"].startswith("text/plain")
        assert response.text == "body"


def test_set_cookie_is_never_replayed():
    """`Set-Cookie` carries per-user state and must not come back from cache."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/login-ish")
    @cache(ttl=60)
    async def login_ish():
        return Response(
            content="ok",
            media_type="text/plain",
            headers={"Set-Cookie": "sid=secret; Path=/", "X-Safe": "yes"},
        )

    assert "sid=secret" in client.get("/login-ish").headers.get("set-cookie", "")

    hit = client.get("/login-ish")
    assert "set-cookie" not in hit.headers
    assert hit.headers["X-Safe"] == "yes"


def test_partial_content_is_not_cached():
    """206 bodies only make sense for the Range request that produced them."""
    app = FastAPI()
    client = TestClient(app)
    calls = {"n": 0}

    @app.get("/chunk")
    @cache(ttl=60)
    async def chunk():
        calls["n"] += 1
        return Response(content="part", status_code=206)

    client.get("/chunk")
    client.get("/chunk")
    assert calls["n"] == 2


def test_no_cache_with_if_none_match_serves_error_instead_of_304():
    """An error response has no validator, so it must not answer 304."""
    app = FastAPI()
    client = TestClient(app)
    state = {"fail": False}

    @app.get("/maybe")
    @cache(no_cache=True)
    async def maybe():
        if state["fail"]:
            return Response(content="gone", status_code=410)
        return Response(content="here", media_type="text/plain")

    etag = client.get("/maybe").headers["ETag"]
    assert client.get("/maybe", headers={"If-None-Match": etag}).status_code == 304

    state["fail"] = True
    response = client.get("/maybe", headers={"If-None-Match": etag})
    assert response.status_code == 410
    assert response.text == "gone"


def test_content_type_header_becomes_media_type_on_hit():
    """A Content-Type set directly as a header survives the round-trip once."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/typed")
    @cache(ttl=60)
    async def typed():
        response = Response(content="x,y")
        response.headers["content-type"] = "text/csv"
        return response

    client.get("/typed")
    hit = client.get("/typed")
    # Exactly one Content-Type header, carrying the handler's value.
    content_types = hit.headers.get_list("content-type")
    assert len(content_types) == 1
    assert content_types[0].startswith("text/csv")


def test_decode_entry_defaults_pre_v2_documents():
    """Documents written before entries carried status/headers still decode."""
    legacy = json.dumps(
        {"fingerprint": 'W/"abc"', "content": "hello", "media_type": "text/plain"}
    )

    entry = decode_entry(legacy)

    assert entry == CacheEntry(
        fingerprint='W/"abc"',
        content=b"hello",
        media_type="text/plain",
        status_code=200,
        headers=None,
    )


@requires_redis
@pytest.mark.asyncio
async def test_redis_reads_pre_v2_documents():
    """A pre-upgrade Redis entry is served as a plain 200."""
    from fastapi_cachex.backends import AsyncRedisCacheBackend

    backend = AsyncRedisCacheBackend(host=REDIS_HOST, port=REDIS_PORT)
    legacy = json.dumps(
        {"fingerprint": 'W/"old"', "content": "legacy", "media_type": "text/plain"}
    )
    await backend.client.set(backend._make_key("legacy-key"), legacy)

    entry = await backend.get("legacy-key")

    assert entry is not None
    assert entry.status_code == 200
    assert entry.headers is None
    assert entry.content == b"legacy"
    await backend.clear()


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_reads_pre_v2_documents():
    """A pre-upgrade Memcached entry is served as a plain 200."""
    from fastapi_cachex.backends import MemcachedBackend

    backend = MemcachedBackend(servers=[MEMCACHED_SERVER])
    await backend.clear()
    legacy = json.dumps(
        {"fingerprint": 'W/"old"', "content": "legacy", "media_type": "text/plain"}
    )
    backend.client.set(backend._make_key("legacy-key"), legacy.encode("utf-8"))

    entry = await backend.get("legacy-key")

    assert entry is not None
    assert entry.status_code == 200
    assert entry.headers is None
    assert entry.content == b"legacy"
    await backend.clear()


@requires_redis
@pytest.mark.asyncio
async def test_redis_round_trips_status_and_headers():
    """Status and headers survive JSON serialization."""
    from fastapi_cachex.backends import AsyncRedisCacheBackend

    backend = AsyncRedisCacheBackend(host=REDIS_HOST, port=REDIS_PORT)
    entry = CacheEntry(
        fingerprint="f",
        content=b"body",
        media_type="text/plain",
        status_code=203,
        headers={"X-Total-Count": "42"},
    )

    await backend.set("v2-key", entry)

    assert await backend.get("v2-key") == entry
    await backend.clear()


@requires_memcached
@pytest.mark.asyncio
async def test_memcached_round_trips_status_and_headers():
    """Status and headers survive JSON serialization."""
    from fastapi_cachex.backends import MemcachedBackend

    backend = MemcachedBackend(servers=[MEMCACHED_SERVER])
    await backend.clear()
    entry = CacheEntry(
        fingerprint="f",
        content=b"body",
        media_type="text/plain",
        status_code=203,
        headers={"X-Total-Count": "42"},
    )

    await backend.set("v2-key", entry)

    assert await backend.get("v2-key") == entry
    await backend.clear()
