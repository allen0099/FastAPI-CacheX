"""A long query string is stored in the key as its SHA-256 digest (#269)."""

import hashlib

from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import CacheKey
from fastapi_cachex import add_routes
from fastapi_cachex import build_cache_key
from fastapi_cachex import cache
from fastapi_cachex import invalidate
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

ENTRY = CacheEntry("e", b"v")


def _request(query: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/p",
            "raw_path": b"/p",
            "query_string": query.encode(),
            "headers": [(b"host", b"h")],
        }
    )


def _query(length: int) -> str:
    """A query that is ``length`` bytes once encoded."""
    return "q=" + "a" * (length - 2)


def _digest(query: str) -> str:
    return "sha256:" + hashlib.sha256(query.encode()).hexdigest()


def test_a_query_of_200_bytes_is_kept() -> None:
    query = _query(200)

    assert CacheKey.from_request(_request(query)).query == query


def test_a_longer_query_is_stored_as_its_digest() -> None:
    query = _query(201)

    key = build_cache_key(_request(query))

    assert key == f"http:v2|GET|h|/p|{_digest(query)}"
    assert CacheKey.parse(key) == CacheKey("GET", "h", "/p", _digest(query))


def test_the_digest_covers_the_encoded_query() -> None:
    """Length and digest are of the encoded query, not the decoded values."""
    encoded = "q=" + "%C3%A9" * 40  # 242 bytes; the decoded value is 40 chars

    assert CacheKey.from_request(_request(encoded)).query == _digest(encoded)


def test_the_digest_is_taken_after_sorting() -> None:
    forward = "a=1&" + _query(250)
    reverse = _query(250) + "&a=1"

    sorted_keys = {
        build_cache_key(_request(q), sort_query=True) for q in (forward, reverse)
    }
    unsorted_keys = {build_cache_key(_request(q)) for q in (forward, reverse)}

    assert len(sorted_keys) == 1
    assert len(unsorted_keys) == 2


def test_extra_components_follow_the_digest() -> None:
    query = _query(201)

    key = build_cache_key(_request(query), "tenant", 7)

    assert key == f"http:v2|GET|h|/p|{_digest(query)}|tenant|7"
    assert CacheKey.parse(key) == CacheKey(
        "GET", "h", "/p", _digest(query), ("tenant", "7")
    )


def test_a_query_cannot_pose_as_a_digest() -> None:
    """``:`` is percent-encoded, so a sent query never starts with ``sha256:``."""
    digest = _digest(_query(201))

    assert CacheKey.from_request(_request(digest)).query != digest


def _app(backend: MemoryBackend) -> tuple[FastAPI, list[int]]:
    app = FastAPI()
    BackendProxy.set(backend)
    calls: list[int] = []

    @app.get("/p")
    @cache(ttl=60)
    async def endpoint() -> dict[str, int]:
        calls.append(1)
        return {"calls": len(calls)}

    add_routes(app, dependencies=[])
    return app, calls


def test_long_queries_are_cached_apart_and_hit_again() -> None:
    backend = MemoryBackend()
    app, calls = _app(backend)
    client = TestClient(app)
    first, second = _query(300), "q=" + "b" * 298

    client.get(f"/p?{first}")
    client.get(f"/p?{second}")
    client.get(f"/p?{first}")

    assert len(calls) == 2
    assert sorted(backend.cache) == sorted(
        f"http:v2|GET|testserver|/p|{_digest(q)}" for q in (first, second)
    )


def test_monitoring_shows_the_digest() -> None:
    backend = MemoryBackend()
    app, _ = _app(backend)
    client = TestClient(app)
    query = _query(300)

    client.get(f"/p?{query}")
    [record] = client.get("/cached-records").json()["cached_records"]

    assert record["path"] == "/p"
    assert record["query_params"] == _digest(query)


async def test_clear_path_counts_a_hashed_query_as_a_query() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    await backend.set(build_cache_key(_request(_query(300))), ENTRY)

    assert await backend.clear_path("/p") == 0
    assert await backend.clear_path("/p", include_params=True) == 1


async def test_invalidate_finds_a_hashed_entry() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    request = _request(_query(300))
    await backend.set(build_cache_key(request), ENTRY)

    assert await invalidate(request) is True
    assert backend.cache == {}


async def test_sorted_invalidate_finds_a_reordered_long_query() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    stored = _request("a=1&" + _query(250))
    await backend.set(build_cache_key(stored, sort_query=True), ENTRY)

    reordered = _request(_query(250) + "&a=1")

    assert await invalidate(reordered) is False
    assert await invalidate(reordered, sort_query=True) is True
    assert backend.cache == {}
