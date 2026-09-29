"""The host in an HTTP cache key is normalised (#265)."""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import CacheKey
from fastapi_cachex import cache
from fastapi_cachex import invalidate
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry


def _request(host: str | None, scheme: str = "http") -> Request:
    headers = [] if host is None else [(b"host", host.encode("latin-1"))]
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "method": "GET",
            "path": "/p",
            "raw_path": b"/p",
            "query_string": b"",
            "headers": headers,
        }
    )


@pytest.mark.parametrize(
    ("host", "scheme", "expected"),
    [
        pytest.param("example.com", "http", "example.com", id="plain"),
        pytest.param("Example.COM", "http", "example.com", id="case"),
        pytest.param("example.com:80", "http", "example.com", id="http-default"),
        pytest.param("example.com:443", "https", "example.com", id="https-default"),
        pytest.param("example.com:443", "http", "example.com:443", id="http-443"),
        pytest.param("example.com:80", "https", "example.com:80", id="https-80"),
        pytest.param("example.com:8080", "http", "example.com:8080", id="other-port"),
        pytest.param("example.com:", "http", "example.com", id="empty-port"),
        pytest.param("Example.com:8080", "https", "example.com:8080", id="case-port"),
        pytest.param("[::1]", "http", "[::1]", id="ipv6"),
        pytest.param("[::1]:80", "http", "[::1]", id="ipv6-default"),
        pytest.param("[::1]:8000", "http", "[::1]:8000", id="ipv6-port"),
        pytest.param("[FE80::1]:443", "https", "[fe80::1]", id="ipv6-case"),
        pytest.param("::1:80", "http", "::1:80", id="ipv6-unbracketed"),
        pytest.param(":80", "http", ":80", id="port-only"),
        pytest.param("example.com.", "http", "example.com.", id="trailing-dot"),
        pytest.param("example.com:443", "wss", "example.com", id="wss-default"),
        pytest.param("example.com:80", "ws", "example.com", id="ws-default"),
        pytest.param("[::1]:", "http", "[::1]", id="ipv6-empty-port"),
        pytest.param("host:80:80", "http", "host:80:80", id="two-ports"),
        pytest.param("HOST:080", "http", "host:080", id="leading-zero-port"),
        pytest.param("", "http", "", id="empty"),
        pytest.param(None, "http", "unknown", id="missing"),
    ],
)
def test_host_is_normalised(host: str | None, scheme: str, expected: str) -> None:
    assert CacheKey.from_request(_request(host, scheme)).host == expected


def test_spellings_of_one_origin_share_an_entry() -> None:
    app = FastAPI()
    backend = MemoryBackend()
    BackendProxy.set(backend)
    calls = 0

    @app.get("/p")
    @cache(ttl=60)
    async def endpoint() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"calls": calls}

    client = TestClient(app)
    for host in ("example.com", "EXAMPLE.com", "example.com:80"):
        assert client.get("/p", headers={"host": host}).json() == {"calls": 1}

    assert list(backend.cache) == ["http:v2|GET|example.com|/p|"]


async def test_invalidate_finds_the_entry_under_another_spelling() -> None:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    await backend.set(
        CacheKey("GET", "example.com", "/p").to_str(), CacheEntry("e", b"v")
    )

    assert await invalidate(_request("Example.com:80")) is True
    assert backend.cache == {}
