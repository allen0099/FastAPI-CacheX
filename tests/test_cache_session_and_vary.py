"""Responses that depend on more than the key are not stored.

A handler that writes the session gets its ``Set-Cookie`` from the session
middleware, outside ``@cache``; a ``Vary`` the handler sets names a header the
default key does not hold.
"""

import secrets

from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from fastapi_cachex import cache


def _session_app(**kwargs: object) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.get("/form")
    @cache(ttl=60, debug_header=True, **kwargs)  # type: ignore[arg-type]
    async def form(request: Request) -> dict[str, str]:
        return {"csrf": request.session.setdefault("csrf", secrets.token_hex(8))}

    @app.get("/plain")
    @cache(ttl=60, debug_header=True)
    async def plain() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_a_session_written_by_the_handler_is_not_stored_or_shared() -> None:
    app = _session_app()
    alice, bob = TestClient(app), TestClient(app)

    first = alice.get("/form")
    second = bob.get("/form")

    assert first.json() != second.json()
    for response in (first, second):
        assert response.headers["X-Cache-Reason"] == "set-cookie"
        assert response.headers["Cache-Control"].startswith("private")
        assert "set-cookie" in response.headers


def test_a_session_left_alone_is_stored_as_before() -> None:
    client = TestClient(_session_app())

    client.get("/plain")

    assert client.get("/plain").headers["X-Cache"] == "HIT"


def _vary_app(header: str, **kwargs: object) -> FastAPI:
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, debug_header=True, **kwargs)  # type: ignore[arg-type]
    async def me(request: Request, response: Response) -> dict[str, str]:
        response.headers["Vary"] = header
        return {"user": request.cookies.get("uid", "anon")}

    return app


def test_a_vary_outside_the_key_is_not_stored() -> None:
    app = _vary_app("Cookie")
    alice, mallory = TestClient(app), TestClient(app)
    alice.cookies.set("uid", "alice")
    mallory.cookies.set("uid", "mallory")

    first = alice.get("/me")
    second = mallory.get("/me")

    assert first.headers["X-Cache-Reason"] == "vary"
    assert second.json() == {"user": "mallory"}


def test_vary_star_is_never_stored() -> None:
    client = TestClient(_vary_app("*", key_builder=lambda request: "fixed"))

    client.get("/me")

    assert client.get("/me").headers["X-Cache-Reason"] == "vary"


def test_a_vary_the_route_keys_on_is_stored() -> None:
    client = TestClient(_vary_app("Accept-Language", vary=["Accept-Language"]))

    client.get("/me")

    assert client.get("/me").headers["X-Cache"] == "HIT"
