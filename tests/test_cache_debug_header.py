"""``@cache(debug_header=True)`` sends ``X-Cache: HIT | MISS | BYPASS`` (#335)."""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError


def _app(**cache_kwargs: Any) -> TestClient:
    app = FastAPI()
    cache_kwargs.setdefault("debug_header", True)

    @app.api_route("/items", methods=["GET", "HEAD", "POST"])
    @cache(**cache_kwargs)
    async def items(request: Request) -> Response:
        body = b"" if request.method == "HEAD" else b'{"items": [1]}'
        return Response(content=body, media_type="application/json")

    return TestClient(app)


def test_off_by_default() -> None:
    client = _app(ttl=60, debug_header=False)
    assert "x-cache" not in client.get("/items").headers
    assert "x-cache" not in client.get("/items").headers


def test_miss_then_hit() -> None:
    client = _app(ttl=60)
    assert client.get("/items").headers["x-cache"] == "MISS"
    assert client.get("/items").headers["x-cache"] == "HIT"


def test_304_from_the_stored_etag_is_a_hit() -> None:
    client = _app(ttl=60)
    etag = client.get("/items").headers["etag"]
    revalidated = client.get("/items", headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.headers["x-cache"] == "HIT"


def test_head_is_served_from_the_get_entry() -> None:
    client = _app(ttl=60)
    assert client.head("/items").headers["x-cache"] == "MISS"
    assert client.get("/items").headers["x-cache"] == "MISS"
    assert client.head("/items").headers["x-cache"] == "HIT"


def test_no_cache_always_misses() -> None:
    client = _app(ttl=60, no_cache=True)
    etag = client.get("/items").headers["etag"]
    assert client.get("/items").headers["x-cache"] == "MISS"
    revalidated = client.get("/items", headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.headers["x-cache"] == "MISS"


@pytest.mark.parametrize(
    "cache_kwargs",
    [
        pytest.param({"no_store": True}, id="no_store"),
        pytest.param({"ttl": 60, "private": True}, id="private"),
        pytest.param({}, id="no-ttl"),
        pytest.param({"ttl": 0}, id="ttl-0"),
    ],
)
def test_a_route_that_never_touches_the_backend_bypasses(
    cache_kwargs: dict[str, Any],
) -> None:
    client = _app(**cache_kwargs)
    assert client.get("/items").headers["x-cache"] == "BYPASS"
    assert client.get("/items").headers["x-cache"] == "BYPASS"


def test_a_304_from_a_fresh_render_is_a_bypass() -> None:
    client = _app()
    etag = client.get("/items").headers["etag"]
    revalidated = client.get("/items", headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.headers["x-cache"] == "BYPASS"


def test_a_request_with_credentials_bypasses_unless_the_route_is_public() -> None:
    headers = {"Authorization": "Bearer t"}
    client = _app(ttl=60)
    assert client.get("/items", headers=headers).headers["x-cache"] == "BYPASS"
    assert client.get("/items", headers=headers).headers["x-cache"] == "BYPASS"

    public = _app(ttl=60, public=True)
    assert public.get("/items", headers=headers).headers["x-cache"] == "MISS"
    assert public.get("/items", headers=headers).headers["x-cache"] == "HIT"


def test_other_methods_get_no_header() -> None:
    client = _app(ttl=60)
    assert "x-cache" not in client.post("/items").headers


def test_a_response_that_is_not_stored_is_a_miss_every_time() -> None:
    app = FastAPI()

    @app.get("/cookie")
    @cache(ttl=60, debug_header=True)
    async def with_cookie(response: Response) -> dict[str, int]:
        response.set_cookie("seen", "1")
        return {"n": 1}

    client = TestClient(app)
    assert client.get("/cookie").headers["x-cache"] == "MISS"
    assert client.get("/cookie").headers["x-cache"] == "MISS"


def test_the_header_is_not_stored_and_replaces_the_handlers_own() -> None:
    app = FastAPI()

    @app.get("/own")
    @cache(ttl=60, debug_header=True)
    async def own() -> Response:
        return Response(content=b"x", headers={"X-Cache": "handler"})

    @app.get("/kept")
    @cache(ttl=60)
    async def kept() -> Response:
        return Response(content=b"x", headers={"X-Cache": "handler"})

    client = TestClient(app)
    assert client.get("/own").headers["x-cache"] == "MISS"
    hit = client.get("/own")
    assert hit.headers["x-cache"] == "HIT"
    assert hit.headers.get_list("x-cache") == ["HIT"]
    # Without the option the handler's own header is left alone.
    assert client.get("/kept").headers["x-cache"] == "handler"
    assert client.get("/kept").headers["x-cache"] == "handler"


def test_debug_header_must_be_a_bool() -> None:
    with pytest.raises(CacheXError, match="debug_header must be a bool, got str"):
        cache(ttl=60, debug_header="yes")(lambda: None)  # type: ignore[arg-type]
