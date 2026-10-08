"""`@cache` must not share a response to a request that arrived with a session (#319).

A non-empty ``request.session``, from any session middleware, is per visitor,
so such a request bypasses the shared backend like one with ``Authorization``.
An empty session and a ``Cookie`` header on its own do not.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import (
    SessionMiddleware as StarletteSessionMiddleware,
)

from fastapi_cachex import cache
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CACHE_KEY_SEPARATOR

_PRIVATE = "private, max-age=60"


def _key(path: str) -> str:
    """The key `default_key_builder` produces for a TestClient GET."""
    return f"http:v2|GET|testserver|{path}|"


def _app(**cache_kwargs: object) -> tuple[FastAPI, dict[str, int]]:
    """A cart kept in Starlette's cookie session, and a route that fills it."""
    app = FastAPI()
    app.add_middleware(StarletteSessionMiddleware, secret_key="s" * 32)
    calls = {"n": 0}

    @app.get("/cart")
    @cache(ttl=60, **cache_kwargs)  # type: ignore[arg-type]
    async def cart(request: Request) -> dict[str, object]:
        calls["n"] += 1
        return {"cart": request.session.get("cart", []), "n": calls["n"]}

    @app.get("/add")
    async def add(request: Request, item: str) -> dict[str, bool]:
        request.session["cart"] = [item]
        return {"ok": True}

    return app, calls


async def test_session_with_data_bypasses_the_backend() -> None:
    """Any session middleware: a non-empty `request.session` is per visitor."""
    app, _ = _app()
    alice = TestClient(app)
    alice.get("/add", params={"item": "apple"})

    response = alice.get("/cart")

    assert response.json()["cart"] == ["apple"]
    assert response.headers["Cache-Control"] == _PRIVATE
    assert await BackendProxy.get().get(_key("/cart")) is None


def test_one_visitors_session_is_not_served_to_the_next() -> None:
    app, _ = _app()
    alice, bob = TestClient(app), TestClient(app)
    alice.get("/add", params={"item": "apple"})
    bob.get("/add", params={"item": "pear"})

    assert alice.get("/cart").json()["cart"] == ["apple"]
    assert bob.get("/cart").json()["cart"] == ["pear"]


def test_empty_session_is_still_cached() -> None:
    app, calls = _app()
    client = TestClient(app)

    client.get("/cart")
    response = client.get("/cart")

    assert calls["n"] == 1
    assert response.headers["Cache-Control"] == "max-age=60"


def test_a_cookie_header_alone_does_not_bypass() -> None:
    """Only a session with data counts; an unrelated cookie keeps the cache."""
    app, calls = _app()

    TestClient(app).get("/cart")
    response = TestClient(app, cookies={"theme": "dark"}).get("/cart")

    assert response.json()["n"] == 1
    assert calls["n"] == 1
    assert response.headers["Cache-Control"] == "max-age=60"


def test_an_x_session_token_header_does_not_bypass() -> None:
    """The removed session middleware's token header is an ordinary header now."""
    app, calls = _app()

    TestClient(app).get("/cart")
    response = TestClient(app).get("/cart", headers={"X-Session-Token": "abc"})

    assert calls["n"] == 1
    assert response.headers["Cache-Control"] == "max-age=60"


def test_public_route_is_shared_across_sessions() -> None:
    """As for `Authorization`: `public` says the response suits every caller."""
    app, calls = _app(public=True)
    alice, bob = TestClient(app), TestClient(app)
    alice.get("/add", params={"item": "apple"})
    bob.get("/add", params={"item": "pear"})

    alice.get("/cart")
    response = bob.get("/cart")

    assert calls["n"] == 1
    assert response.headers["Cache-Control"] == "public, max-age=60"


async def test_cache_authorized_caches_per_session_entries() -> None:
    def per_visitor_key(request: Request) -> str:
        visitor = request.session.get("cart", ["-"])[0]
        return f"{request.url.path}{CACHE_KEY_SEPARATOR}{visitor}"

    app, _ = _app(key_builder=per_visitor_key, cache_authorized=True)
    alice, bob = TestClient(app), TestClient(app)
    alice.get("/add", params={"item": "apple"})
    bob.get("/add", params={"item": "pear"})

    alice.get("/cart")
    response = bob.get("/cart")

    assert response.json()["cart"] == ["pear"]
    assert response.headers["Cache-Control"] == _PRIVATE
    assert await BackendProxy.get().get("/cart|apple") is not None
    assert await BackendProxy.get().get("/cart|pear") is not None


def test_bypass_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    app, _ = _app()
    client = TestClient(app)
    client.get("/add", params={"item": "apple"})

    with caplog.at_level(logging.DEBUG, logger="fastapi_cachex.cache"):
        client.get("/cart")

    assert "Session data present; bypassing the backend for path=/cart" in caplog.text
