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

from fastapi_cachex import BackendProxy
from fastapi_cachex import build_cache_key
from fastapi_cachex import cache
from fastapi_cachex import invalidate
from fastapi_cachex import invalidate_path
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import CacheEntry


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
        # The mount prefix is part of the key, and of `request.url.path`.
        path = f"{request.scope.get('root_path', '')}/items/{name}"
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


def _scope(method: str, path: str, query: bytes = b"") -> dict[str, object]:
    """A scope as a server delivers it: ``path`` already percent-decoded."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": b"/unrelated",
        "query_string": query,
        "headers": [(b"host", b"testserver")],
    }


async def _store(request: Request, **key_options: bool) -> None:
    key = build_cache_key(request, **key_options)
    await BackendProxy.get().set(key, CacheEntry(fingerprint="etag", content=b"x"), 60)


@pytest.mark.parametrize(
    ("served", "given"),
    [
        ("/items/50%25", "/items/50%25"),
        ("/items/a%41", "/items/a%41"),
        ("/items/a b", "/items/a b"),
        ("/items/a#b", "/items/a#b"),
        ("/items/a?b", "/items/a%3Fb"),
    ],
)
async def test_the_path_is_the_decoded_one_the_handler_sees(
    served: str, given: str
) -> None:
    """`TestClient` decodes twice and hides this, so the scopes are built here.

    A client that sent ``/items/50%2525`` reaches the handler as
    ``/items/50%25``; decoding that again would look for ``/items/50%``.
    """
    await _store(Request(_scope("GET", served)))

    assert await invalidate_path(Request(_scope("PUT", "/other")), given) is True


async def test_sort_query_is_passed_on() -> None:
    await _store(Request(_scope("GET", "/q", b"b=2&a=1")), sort_query=False)
    caller = Request(_scope("PUT", "/other"))

    # Sorted (the default) builds the key of ?a=1&b=2, which was never stored.
    assert await invalidate_path(caller, "/q?b=2&a=1") is False
    assert await invalidate_path(caller, "/q?b=2&a=1", sort_query=False) is True


async def test_a_list_value_becomes_repeated_parameters() -> None:
    await _store(Request(_scope("GET", "/q", b"tag=a&tag=b")))

    caller = Request(_scope("PUT", "/other"))
    assert await invalidate_path(caller, "/q", query={"tag": ["a", "b"]}) is True


async def test_a_custom_key_builder_is_not_told_its_key_cannot_match(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only the default key holds the method; a custom one may match a PUT."""
    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.cache"):
        missing = await invalidate(
            Request(_scope("PUT", "/thing")), lambda request: "custom"
        )

    assert missing is False
    assert caplog.text == ""
