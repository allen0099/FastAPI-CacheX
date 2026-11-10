"""``invalidate_path()``: drop a cached GET from the handler that changes it (#332).

The documented recipe copied the ASGI scope and rewrote ``method``, ``path``
and ``query_string`` by hand, and ``invalidate(request)`` with the ``PUT``
request itself quietly returned ``False``.
"""

import logging
from collections.abc import Awaitable
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import build_cache_key
from fastapi_cachex import cache
from fastapi_cachex import invalidate
from fastapi_cachex import invalidate_path
from fastapi_cachex.exceptions import CacheXError


def make_app() -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/items/{name:path}")
    @cache(ttl=60, debug_header=True)
    async def read_item(name: str) -> dict[str, int]:
        calls["n"] += 1
        return {"calls": calls["n"]}

    @app.put("/items/{name:path}")
    async def update_item(name: str, request: Request) -> dict[str, bool]:
        # `url_for` includes the prefix an app is mounted under.
        path = request.url_for("read_item", name=name).path
        query = request.url.query or None
        return {"invalidated": await invalidate_path(request, path, query=query)}

    return app, calls


@pytest.mark.parametrize(
    "path", ["/items/1", "/items/a b", "/items/caf%C3%A9", "/items/50%25"]
)
def test_the_cached_get_is_dropped(path: str) -> None:
    app, _ = make_app()
    client = TestClient(app)
    client.get(path)
    assert client.get(path).headers["X-Cache"] == "HIT"

    assert client.put(path).json() == {"invalidated": True}
    # Nothing is left to delete.
    assert client.put(path).json() == {"invalidated": False}

    assert client.get(path).headers["X-Cache"] == "MISS"


def test_only_the_entry_for_that_query_is_dropped() -> None:
    app, _ = make_app()
    client = TestClient(app)
    client.get("/items/1")
    client.get("/items/1?b=2&a=1")

    assert client.put("/items/1?a=1&b=2").json() == {"invalidated": True}

    assert client.get("/items/1?b=2&a=1").headers["X-Cache"] == "MISS"
    assert client.get("/items/1").headers["X-Cache"] == "HIT"


@pytest.mark.parametrize(
    "call",
    [
        lambda request: invalidate_path(request, "/q?a=1&b=2"),
        lambda request: invalidate_path(request, "/q", query="a=1&b=2"),
        lambda request: invalidate_path(request, "/q", query="?b=2&a=1"),
        lambda request: invalidate_path(request, "/q", query={"a": 1, "b": 2}),
        lambda request: invalidate_path(request, "/q", query=[("b", "2"), ("a", "1")]),
    ],
    ids=["in-path", "string", "leading-question-mark", "mapping", "pairs"],
)
def test_the_query_can_be_given_in_several_forms(
    call: Callable[[Request], Awaitable[bool]],
) -> None:
    app = FastAPI()

    @app.get("/q")
    @cache(ttl=60, debug_header=True)
    async def q() -> dict[str, str]:
        return {}

    @app.post("/drop")
    async def drop(request: Request) -> bool:
        return await call(request)

    client = TestClient(app)
    client.get("/q?a=1&b=2")

    assert client.post("/drop").json() is True
    assert client.get("/q?a=1&b=2").headers["X-Cache"] == "MISS"


def test_vary_and_key_builder_are_honoured() -> None:
    app = FastAPI()

    def per_tenant(request: Request) -> str:
        return build_cache_key(request, request.headers.get("x-tenant", ""))

    @app.get("/report")
    @cache(ttl=60, key_builder=per_tenant, vary=["Accept-Language"], debug_header=True)
    async def report() -> dict[str, str]:
        return {}

    @app.post("/report")
    async def rebuild(request: Request) -> bool:
        return await invalidate_path(
            request, "/report", per_tenant, ["Accept-Language"]
        )

    client = TestClient(app)
    mine = {"X-Tenant": "a", "Accept-Language": "en"}
    theirs = {"X-Tenant": "b", "Accept-Language": "en"}
    client.get("/report", headers=mine)
    client.get("/report", headers=theirs)

    # The caller's own headers select the variant, as with invalidate().
    assert client.post("/report", headers=mine).json() is True

    assert client.get("/report", headers=mine).headers["X-Cache"] == "MISS"
    assert client.get("/report", headers=theirs).headers["X-Cache"] == "HIT"


def test_a_mounted_app_passes_the_path_it_was_requested_under() -> None:
    """The key holds the full path, mount prefix included."""
    sub, _ = make_app()
    app = FastAPI()
    app.mount("/api", sub)
    client = TestClient(app)
    client.get("/api/items/1")
    assert client.get("/api/items/1").headers["X-Cache"] == "HIT"

    assert client.put("/api/items/1").json() == {"invalidated": True}
    assert client.get("/api/items/1").headers["X-Cache"] == "MISS"


@pytest.mark.parametrize(
    ("path", "query", "message"),
    [
        ("items/1", None, "path must start with '/'"),
        ("", None, "path must start with '/'"),
        ("/items/1?a=1", "b=2", "not both"),
    ],
)
async def test_arguments_that_cannot_name_a_request_are_rejected(
    path: str, query: str | None, message: str
) -> None:
    request = Request({"type": "http", "method": "PUT", "path": "/", "headers": []})

    with pytest.raises(CacheXError, match=message):
        await invalidate_path(request, path, query=query)


def test_invalidate_with_the_writing_request_says_why_it_deleted_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """It builds the PUT key, which no cached GET has (#332)."""
    app = FastAPI()

    @app.get("/thing")
    @cache(ttl=60, debug_header=True)
    async def thing() -> dict[str, str]:
        return {}

    @app.put("/thing")
    async def change(request: Request) -> bool:
        return await invalidate(request)

    client = TestClient(app)
    client.get("/thing")

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.cache"):
        assert client.put("/thing").json() is False

    assert "invalidate() was given a PUT request" in caplog.text
    assert "invalidate_path" in caplog.text
    assert client.get("/thing").headers["X-Cache"] == "HIT"


async def test_a_get_that_finds_nothing_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/nothing",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
        }
    )

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.cache"):
        assert await invalidate(request) is False

    assert caplog.text == ""
