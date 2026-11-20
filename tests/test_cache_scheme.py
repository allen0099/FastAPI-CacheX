"""The scheme is part of the HTTP cache key (#576).

A response cached for a plain ``http`` request was served to the next
``https`` request of the same URL, absolute links built from the request URL
included. A write still makes both stale, so invalidation clears both.
"""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import cache
from fastapi_cachex import invalidate
from fastapi_cachex import invalidate_path
from fastapi_cachex import set_event_handler
from fastapi_cachex.cache_key import CacheKey
from fastapi_cachex.routes import CachedRecord
from fastapi_cachex.routes import CacheHitRecord
from fastapi_cachex.types import CacheEntry


def make_app(key_builder=None) -> FastAPI:
    app = FastAPI()
    kwargs = {} if key_builder is None else {"key_builder": key_builder}

    @app.get("/page")
    @cache(ttl=60, debug_header=True, **kwargs)
    async def page(request: Request) -> dict[str, str]:
        return {"next": str(request.url_for("page"))}

    @app.put("/page")
    async def update(request: Request) -> dict[str, bool]:
        return {"invalidated": await invalidate_path(request, "/page", **kwargs)}

    @app.post("/invalidate-get")
    async def invalidate_get(request: Request) -> dict[str, bool]:
        scope = {**request.scope, "method": "GET", "path": "/page", "raw_path": None}
        return {"invalidated": await invalidate(Request(scope), **kwargs)}

    return app


def test_an_http_response_is_not_served_to_https() -> None:
    app = make_app()
    plain = TestClient(app, base_url="http://example.com")
    secure = TestClient(app, base_url="https://example.com")

    assert plain.get("/page").json() == {"next": "http://example.com/page"}
    response = secure.get("/page")
    assert response.headers["X-Cache"] == "MISS"
    assert response.json() == {"next": "https://example.com/page"}
    assert secure.get("/page").headers["X-Cache"] == "HIT"
    assert plain.get("/page").json() == {"next": "http://example.com/page"}


def test_the_key_holds_the_scheme() -> None:
    key = CacheKey(method="GET", host="h", path="/", scheme="https").to_str()

    assert key == "http:v3|https|GET|h|/|"
    assert CacheKey.parse(key) == CacheKey("GET", "h", "/", scheme="https")


def test_a_key_built_by_hand_must_name_the_scheme() -> None:
    with pytest.raises(TypeError, match="scheme"):
        CacheKey("GET", "h", "/")  # type: ignore[call-arg]


def test_a_key_of_the_previous_format_is_not_an_http_key() -> None:
    """``http:v2`` keys (0.4.0 to 0.6.x) are not read, cleared or listed."""
    assert CacheKey.parse("http:v2|GET|h|/p|") is None
    assert CacheKey.parse("http:v2|GET|h|/p|q=1|extra") is None


@pytest.mark.parametrize("writer", ["http", "https"])
@pytest.mark.parametrize("route", ["/page", "/invalidate-get"])
def test_invalidation_clears_both_schemes(writer: str, route: str) -> None:
    """A write over either scheme makes both entries stale (#576)."""
    app = make_app()
    clients = {
        scheme: TestClient(app, base_url=f"{scheme}://example.com")
        for scheme in ("http", "https")
    }
    for client in clients.values():
        client.get("/page")
        assert client.get("/page").headers["X-Cache"] == "HIT"
    events: list[str] = []
    set_event_handler(lambda event: events.append(event.key or ""))
    try:
        method = "put" if route == "/page" else "post"
        response = getattr(clients[writer], method)(route)
    finally:
        set_event_handler(None)

    assert response.json() == {"invalidated": True}
    assert len(events) == 2
    assert set(events) == {
        "http:v3|http|GET|example.com|/page|",
        "http:v3|https|GET|example.com|/page|",
    }
    for client in clients.values():
        assert client.get("/page").headers["X-Cache"] == "MISS"


async def test_invalidation_reports_an_entry_of_the_other_scheme() -> None:
    """Only the other scheme's entry exists: still a deletion."""
    app = make_app()
    TestClient(app, base_url="http://example.com").get("/page")

    response = TestClient(app, base_url="https://example.com").put("/page")

    assert response.json() == {"invalidated": True}
    assert await BackendProxy.get().get_all_keys() == []


def test_a_builder_without_the_scheme_is_deleted_once() -> None:
    """One key for both schemes: one delete, one event."""
    app = make_app(key_builder=lambda request: f"page:{request.url.path}")
    plain = TestClient(app, base_url="http://example.com")
    plain.get("/page")
    events: list[str] = []
    set_event_handler(lambda event: events.append(event.key or ""))
    try:
        assert plain.put("/page").json() == {"invalidated": True}
    finally:
        set_event_handler(None)

    assert events == ["page:/page"]


async def test_clear_path_clears_both_schemes() -> None:
    backend = BackendProxy.get()
    entry = CacheEntry(fingerprint="e", content=b"v")
    for scheme in ("http", "https"):
        await backend.set(
            CacheKey("GET", "example.com", "/page", scheme=scheme).to_str(), entry
        )

    assert await backend.clear_path("/page") == 2


@pytest.mark.parametrize(
    ("writer", "host"),
    [("http", "example.com:80"), ("https", "example.com:443")],
)
def test_a_default_port_in_host_still_reaches_the_other_scheme(
    writer: str, host: str
) -> None:
    """The port the writer's scheme implies is not carried to the other one."""
    app = make_app()
    other = "https" if writer == "http" else "http"
    reader = TestClient(app, base_url=f"{other}://example.com")
    reader.get("/page")
    assert reader.get("/page").headers["X-Cache"] == "HIT"

    response = TestClient(app, base_url=f"{writer}://example.com").put(
        "/page", headers={"Host": host}
    )

    assert response.json() == {"invalidated": True}
    assert reader.get("/page").headers["X-Cache"] == "MISS"


@pytest.mark.parametrize(
    ("record", "fields"),
    [
        (CacheHitRecord, ("k", "GET", "h", "/p", "", "e", False, 1.0)),
        (
            CachedRecord,
            ("k", "GET", "h", "/p", "", "e", "bytes", 1, False, 1.0, None, None),
        ),
    ],
)
def test_a_record_built_by_position_must_name_the_scheme(record, fields) -> None:
    """A 0.6.x positional call fails instead of shifting every field (#623)."""
    with pytest.raises(TypeError, match="scheme"):
        record(*fields)

    built = record(*fields, scheme="https")
    assert (built.scheme, built.method, built.host) == ("https", "GET", "h")
