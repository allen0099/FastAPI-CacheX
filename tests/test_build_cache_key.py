"""``build_cache_key`` builds the default key plus escaped extra components (#264)."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

import fastapi_cachex
from fastapi_cachex import add_routes
from fastapi_cachex import build_cache_key
from fastapi_cachex import cache
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.cache import default_key_builder
from fastapi_cachex.cache_key import CacheKey
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CACHE_KEY_SEPARATOR
from fastapi_cachex.types import escape_key_component


def _request(
    path: str = "/items",
    query: bytes = b"",
    host: str | None = "example.com",
    method: str = "GET",
) -> Request:
    headers = [] if host is None else [(b"host", host.encode())]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": headers,
        }
    )


def _key_before_264(request: Request) -> str:
    """``default_key_builder`` as it was written before ``build_cache_key``.

    Only the format tag (#266) is added.
    """
    return (
        f"http:v2{CACHE_KEY_SEPARATOR}{request.method}{CACHE_KEY_SEPARATOR}"
        f"{escape_key_component(request.headers.get('host', 'unknown'))}"
        f"{CACHE_KEY_SEPARATOR}"
        f"{escape_key_component(request.url.path)}{CACHE_KEY_SEPARATOR}"
        f"{request.query_params}"
    )


REQUESTS = [
    pytest.param({}, id="plain"),
    pytest.param({"query": b"b=2&a=1&a=3"}, id="query"),
    pytest.param({"host": "evil|||host:8000", "path": "/a|||b/100%"}, id="escaped"),
    pytest.param({"host": None}, id="no-host"),
    pytest.param({"method": "HEAD", "query": b"q=%7C%7C%7C"}, id="head"),
]


@pytest.mark.parametrize("kwargs", REQUESTS)
def test_without_components_the_key_is_unchanged(kwargs: dict[str, object]) -> None:
    request = _request(**kwargs)  # type: ignore[arg-type]

    assert build_cache_key(request) == _key_before_264(request)
    assert default_key_builder(request) == build_cache_key(request)


def test_default_key_is_pinned() -> None:
    request = _request(path="/a|b", query=b"x=1", host="h:1")

    assert build_cache_key(request) == "http:v2|GET|h:1|/a%7Cb|x=1"


def test_components_are_appended_after_the_query() -> None:
    request = _request(query=b"x=1")

    assert build_cache_key(request, "user-1", 42) == (
        "http:v2|GET|example.com|/items|x=1|user-1|42"
    )


def test_int_and_str_components_are_the_same() -> None:
    request = _request()

    assert build_cache_key(request, 7) == build_cache_key(request, "7")


def test_empty_component_is_a_component() -> None:
    request = _request()

    assert build_cache_key(request, "") == build_cache_key(request) + "|"
    assert build_cache_key(request, "") != build_cache_key(request)


def test_components_cannot_inject_the_separator() -> None:
    """A component containing ``|||`` cannot line up with another's key."""
    request = _request()

    injected = build_cache_key(request, "a|||b")
    assert injected == build_cache_key(request) + "|a%7C%7C%7Cb"
    assert injected != build_cache_key(request, "a", "b")
    assert build_cache_key(request, "100%7C") != build_cache_key(request, "100|")


@pytest.mark.parametrize("bad", [None, True, 1.5, b"x", ["x"]])
def test_other_component_types_are_rejected(bad: object) -> None:
    with pytest.raises(TypeError, match="must be str or int"):
        build_cache_key(_request(), bad)  # type: ignore[arg-type]


def test_build_cache_key_is_exported() -> None:
    assert "build_cache_key" in fastapi_cachex.__all__
    assert fastapi_cachex.build_cache_key is build_cache_key


def test_keys_with_components_split_into_query_and_extras() -> None:
    key = build_cache_key(_request(path="/p|q", query=b"x=1"), "a|b", 3)

    assert CacheKey.parse(key) == CacheKey(
        "GET", "example.com", "/p|q", "x=1", ("a|b", "3")
    )
    parsed = CacheKey.parse(build_cache_key(_request(), "u"))
    assert parsed is not None
    assert (parsed.query, parsed.extra) == ("", ("u",))


@pytest.fixture
def backend() -> Iterator[MemoryBackend]:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    yield backend
    BackendProxy.set(None)


def _per_user_app() -> FastAPI:
    app = FastAPI()

    def per_user_key(request: Request) -> str:
        return build_cache_key(request, request.headers.get("x-test-user", "anon"))

    @app.get("/me")
    @cache(ttl=60, key_builder=per_user_key)
    async def me(request: Request) -> dict[str, str]:
        return {"user": request.headers.get("x-test-user", "anon")}

    add_routes(app, prefix="/cache", dependencies=[])
    return app


async def test_per_user_entries_are_separate_and_cleared_by_path(
    backend: MemoryBackend,
) -> None:
    client = TestClient(_per_user_app())

    assert client.get("/me", headers={"x-test-user": "a"}).json() == {"user": "a"}
    assert client.get("/me", headers={"x-test-user": "b"}).json() == {"user": "b"}
    client.get("/me", params={"page": "2"}, headers={"x-test-user": "a"})
    assert len(backend.cache) == 3

    # Only the two entries without a query string.
    assert await backend.clear_path("/me") == 2
    assert await backend.clear_path("/me", include_params=True) == 1
    assert backend.cache == {}


def test_monitoring_routes_show_extra_components(backend: MemoryBackend) -> None:
    client = TestClient(_per_user_app())
    client.get("/me", params={"page": "2"}, headers={"x-test-user": "a|b"})

    [hit] = client.get("/cache/cached-hits").json()["cached_hits"]
    [record] = client.get("/cache/cached-records").json()["cached_records"]

    for item in (hit, record):
        assert item["path"] == "/me"
        assert item["query_params"] == "page=2"
        assert item["extra_components"] == ["a|b"]
