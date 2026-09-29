"""Tests for ``sort_query`` (#267), on by default since #72."""

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.cache import build_cache_key
from fastapi_cachex.cache import cache
from fastapi_cachex.cache import default_key_builder
from fastapi_cachex.cache import invalidate
from fastapi_cachex.cache_key import CacheKey
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry


def _request(query: bytes) -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/items",
            "query_string": query,
            "headers": [(b"host", b"testserver")],
        }
    )


def _query(key: str) -> str:
    parsed = CacheKey.parse(key)
    assert parsed is not None
    return parsed.query


def _sorted_query(query: bytes) -> str:
    return _query(build_cache_key(_request(query)))


def _app(
    *, sort_query: bool | None = None, sync: bool = False
) -> tuple[TestClient, list[str]]:
    app = FastAPI()
    calls: list[str] = []

    if sync:

        @app.get("/items")
        @cache(ttl=60, sort_query=sort_query)
        def sync_items(request: Request) -> dict[str, str]:
            calls.append(request.url.query)
            return {"query": request.url.query}

    else:

        @app.get("/items")
        @cache(ttl=60, sort_query=sort_query)
        async def async_items(request: Request) -> dict[str, str]:
            calls.append(request.url.query)
            return {"query": request.url.query}

    return TestClient(app), calls


@pytest.mark.parametrize("sync", [False, True], ids=["async", "sync"])
@pytest.mark.parametrize("sort_query", [None, True], ids=["default", "explicit"])
def test_reordered_parameters_share_one_entry(
    sync: bool, sort_query: bool | None
) -> None:
    client, calls = _app(sort_query=sort_query, sync=sync)

    first = client.get("/items?limit=1&q=wid")
    second = client.get("/items?q=wid&limit=1")

    assert first.json() == second.json() == {"query": "limit=1&q=wid"}
    assert calls == ["limit=1&q=wid"]


@pytest.mark.parametrize("sync", [False, True], ids=["async", "sync"])
def test_repeated_name_order_stays_distinct(sync: bool) -> None:
    client, calls = _app(sync=sync)

    client.get("/items?tag=b&tag=a")
    client.get("/items?tag=a&tag=b")
    # Moving another name around a repeated one does not reorder its values.
    client.get("/items?x=1&tag=b&tag=a")
    client.get("/items?tag=b&x=1&tag=a")

    assert calls == ["tag=b&tag=a", "tag=a&tag=b", "x=1&tag=b&tag=a"]


@pytest.mark.parametrize("sync", [False, True], ids=["async", "sync"])
def test_sort_query_false_keeps_reordered_queries_apart(sync: bool) -> None:
    client, calls = _app(sort_query=False, sync=sync)

    client.get("/items?a=1&b=2")
    client.get("/items?b=2&a=1")
    client.get("/items?a=1&b=2")

    assert calls == ["a=1&b=2", "b=2&a=1"]


def test_only_parameter_order_merges() -> None:
    client, calls = _app()

    for query in ("a=1&b=2", "b=2&a=1", "a=1&b=3", "a=1", "b=2", "a=1&b=2&c="):
        client.get(f"/items?{query}")

    assert calls == ["a=1&b=2", "a=1&b=3", "a=1", "b=2", "a=1&b=2&c="]


def test_the_default_key_is_the_sorted_one() -> None:
    for query in (b"b=2&a=1", b"tag=b&tag=a", b"q=a%20b&n%26=x%3D", b""):
        request = _request(query)
        assert default_key_builder(request) == build_cache_key(request)
        assert build_cache_key(request) == build_cache_key(request, sort_query=True)


def test_the_unsorted_key_keeps_the_query_as_sent() -> None:
    for query in (b"b=2&a=1", b"tag=b&tag=a", b"q=a%20b&n%26=x%3D", b""):
        request = _request(query)
        key = build_cache_key(request, sort_query=False)
        assert _query(key) == str(request.query_params)


def test_sorted_key_matches_the_unsorted_for_a_sorted_query() -> None:
    request = _request(b"a=1&b=x%26y&c=")

    assert build_cache_key(request) == build_cache_key(request, sort_query=False)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (b"", ""),
        (b"&", ""),
        (b"b=2&&a=1", "a=1&b=2"),
        (b"b&a", "a=&b="),
        (b"b=&a=1", "a=1&b="),
        (b"b=2&a=1&a=", "a=1&a=&b=2"),
        (b"b=1&a=2&b=0", "a=2&b=1&b=0"),
    ],
    ids=[
        "empty",
        "only-separator",
        "empty-segment",
        "no-equals",
        "blank-value",
        "repeated-with-blank",
        "repeated-split",
    ],
)
def test_edge_cases(query: bytes, expected: str) -> None:
    assert _sorted_query(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (b"z=a%20b&n%26=x%3Dy", "n%26=x%3Dy&z=a+b"),
        (b"z=%E4%B8%AD&a=%7C", "a=%7C&z=%E4%B8%AD"),
    ],
)
def test_percent_encoding_is_kept(query: bytes, expected: str) -> None:
    request = _request(query)

    assert _sorted_query(query) == expected
    # Each pair is encoded exactly as the unsorted key encodes it.
    assert sorted(expected.split("&")) == sorted(str(request.query_params).split("&"))


def test_names_are_compared_decoded() -> None:
    # `%61` is `a`: the key already writes it as `a`, so it sorts as `a`,
    # before `b`, and keeps its place among the other `a` values.
    assert _sorted_query(b"b=0&%61=1&a=2") == "a=1&a=2&b=0"


@pytest.mark.parametrize("value", [True, False])
def test_sort_query_with_custom_key_builder_is_rejected(value: bool) -> None:
    # False is rejected too: build_cache_key() in the builder sorts anyway.
    with pytest.raises(CacheXError, match="sort_query only applies"):
        cache(ttl=60, key_builder=default_key_builder, sort_query=value)(lambda: None)


@pytest.mark.parametrize("value", [1, 0, "yes"])
def test_sort_query_must_be_a_bool(value: object) -> None:
    with pytest.raises(CacheXError, match="sort_query must be a bool"):
        cache(ttl=60, sort_query=value)(lambda: None)  # type: ignore[arg-type]


def test_custom_key_builder_can_sort_through_build_cache_key() -> None:
    app = FastAPI()
    calls: list[int] = []

    def sorted_key(request: Request) -> str:
        return build_cache_key(request, "tenant")

    @app.get("/items")
    @cache(ttl=60, key_builder=sorted_key)
    async def items() -> dict[str, int]:
        calls.append(1)
        return {}

    client = TestClient(app)
    client.get("/items?a=1&b=2")
    client.get("/items?b=2&a=1")

    assert calls == [1]


async def test_invalidate_with_reordered_query_drops_the_entry() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    client, calls = _app()
    client.get("/items?a=1&b=2")

    assert await invalidate(_request(b"b=2&a=1")) is True
    assert not backend.cache
    client.get("/items?a=1&b=2")
    assert calls == ["a=1&b=2", "a=1&b=2"]


async def test_invalidate_needs_the_routes_sort_query() -> None:
    # invalidate() cannot read the route's settings: without the flag it
    # builds the sorted key and misses the entry an unsorted route stored.
    backend = MemoryBackend()
    BackendProxy.set(backend)
    client, _ = _app(sort_query=False)
    client.get("/items?b=2&a=1")

    assert await invalidate(_request(b"b=2&a=1")) is False
    assert len(backend.cache) == 1
    assert await invalidate(_request(b"b=2&a=1"), sort_query=False) is True
    assert not backend.cache


@pytest.mark.parametrize(
    "call",
    [
        lambda: invalidate(
            _request(b""), key_builder=default_key_builder, sort_query=True
        ),
        lambda: invalidate(
            _request(b""), key_builder=default_key_builder, sort_query=False
        ),
        lambda: invalidate(_request(b""), sort_query=1),  # type: ignore[arg-type]
    ],
    ids=["true-with-key-builder", "false-with-key-builder", "not-a-bool"],
)
async def test_invalidate_validates_sort_query(
    call: Callable[[], object],
) -> None:
    with pytest.raises(CacheXError):
        await call()  # type: ignore[misc]


async def test_clear_path_clears_sorted_entries() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    client, _ = _app()
    client.get("/items?b=2&a=1")
    client.get("/items")

    assert await backend.clear_path("/items") == 1
    assert await backend.clear_path("/items", include_params=True) == 1
    assert not backend.cache


async def test_invalidate_with_a_custom_key_builder_and_no_sort_query() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    request = _request(b"b=2&a=1")
    await backend.set(build_cache_key(request, "t"), CacheEntry("e", b"v"))

    def tenant_key(request: Request) -> str:
        return build_cache_key(request, "t")

    assert await invalidate(_request(b"a=1&b=2"), key_builder=tenant_key) is True
    assert not backend.cache
