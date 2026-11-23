"""Responses that depend on more than the key are not stored.

A handler that writes the session gets its ``Set-Cookie`` from the session
middleware, outside ``@cache``; a ``Vary`` the handler sets names a header the
default key does not hold.
"""

import logging
import secrets
import threading
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.middleware import sessions
from starlette.middleware.sessions import SessionMiddleware

from fastapi_cachex import _stored_response as stored_response
from fastapi_cachex import cache
from tests.conftest import Clock


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


def test_a_stale_answer_after_a_session_write_is_private(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stale="error"``: the handler wrote the session, then failed."""
    monkeypatch.setattr(stored_response, "_now", clock.time)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")
    failing = [False]

    @app.get("/page")
    @cache(ttl=1, stale="error", stale_ttl=60, serve_stale=True, debug_header=True)
    async def page(request: Request) -> dict[str, str]:
        if failing[0]:
            request.session["csrf"] = "minted"
            raise HTTPException(status_code=503)
        return {"ok": "yes"}

    TestClient(app).get("/page")
    clock.advance(2)
    failing[0] = True

    response = TestClient(app).get("/page")

    assert response.headers["X-Cache"] == "STALE"
    assert response.headers["Cache-Control"].startswith("private")


def test_a_session_that_cannot_be_copied_is_served_private() -> None:
    """A custom middleware's session may hold anything; the request still works."""
    app = FastAPI()

    @app.middleware("http")
    async def session(request: Request, call_next):
        request.scope["session"] = {"lock": threading.Lock()}
        return await call_next(request)

    @app.get("/shared")
    @cache(ttl=60, public=True, debug_header=True)
    async def shared() -> dict[str, str]:
        return {"ok": "yes"}

    response = TestClient(app).get("/shared")

    assert response.status_code == 200
    assert response.headers["X-Cache-Reason"] == "set-cookie"


def test_a_vary_outside_the_key_warns_once_and_names_the_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without it the route silently stops caching after the upgrade."""
    client = TestClient(_vary_app("Accept"))

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex"):
        client.get("/me")
        client.get("/me")

    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1
    assert "Vary: 'accept'" in warnings[0]
    assert "vary=" in warnings[0]


@pytest.mark.filterwarnings("ignore:cache vary on Cookie")
def test_a_session_written_by_a_dependency_is_not_stored_or_shared() -> None:
    """#653: the copy is taken after the dependencies, so it held their write."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    async def csrf(request: Request) -> str:
        return str(request.session.setdefault("csrf", secrets.token_hex(8)))

    @app.get("/form")
    @cache(ttl=60, cache_authorized=True, vary=["Cookie"], debug_header=True)
    async def form(token: Annotated[str, Depends(csrf)]) -> dict[str, str]:
        return {"csrf": token}

    first = TestClient(app).get("/form")
    second = TestClient(app).get("/form")

    assert first.json() != second.json()
    for response in (first, second):
        assert response.headers["X-Cache"] == "MISS"
        assert response.headers["X-Cache-Reason"] == "set-cookie"
        assert response.headers["Cache-Control"].startswith("private")


def test_a_session_rewritten_with_the_same_value_is_not_sent_public() -> None:
    """#653: Starlette sends the cookie for a write that changes nothing."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.get("/login")
    async def login(request: Request) -> dict[str, str]:
        request.session["currency"] = "usd"
        return {}

    @app.get("/price")
    @cache(ttl=60, public=True, debug_header=True)
    async def price(request: Request) -> dict[str, str]:
        request.session["currency"] = "usd"
        return {"currency": "usd"}

    alice = TestClient(app)
    alice.get("/login")
    response = alice.get("/price")

    assert "set-cookie" in response.headers
    assert response.headers["X-Cache-Reason"] == "set-cookie"
    assert response.headers["Cache-Control"].startswith("private")
    assert TestClient(app).get("/price").headers["X-Cache"] == "MISS"


def _plain_session_app(session: dict[str, object]) -> FastAPI:
    """A session middleware that keeps a dict, which records no writes."""
    app = FastAPI()

    @app.middleware("http")
    async def middleware(request: Request, call_next):
        request.scope["session"] = dict(session)
        return await call_next(request)

    @app.get("/shared")
    @cache(ttl=60, public=True, debug_header=True)
    async def shared() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_a_non_empty_session_without_a_modified_flag_counts_as_written() -> None:
    """#653: Starlette before 1.0 sends the cookie for every such response."""
    client = TestClient(_plain_session_app({"user": "alice"}))

    for _ in range(2):
        response = client.get("/shared")
        assert response.headers["X-Cache"] == "MISS"
        assert response.headers["X-Cache-Reason"] == "set-cookie"


def test_an_empty_session_without_a_modified_flag_is_stored() -> None:
    client = TestClient(_plain_session_app({}))

    client.get("/shared")

    assert client.get("/shared").headers["X-Cache"] == "HIT"


@pytest.mark.skipif(
    not hasattr(sessions, "Session"), reason="Starlette before 1.0 flags no writes"
)
@pytest.mark.filterwarnings("ignore:cache vary on Cookie")
def test_a_session_read_but_not_written_is_stored() -> None:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.get("/login")
    async def login(request: Request) -> dict[str, str]:
        request.session["user"] = "alice"
        return {}

    @app.get("/me")
    @cache(ttl=60, cache_authorized=True, vary=["Cookie"], debug_header=True)
    async def me(request: Request) -> dict[str, str]:
        return {"user": request.session["user"]}

    alice = TestClient(app)
    alice.get("/login")
    alice.get("/me")

    assert alice.get("/me").headers["X-Cache"] == "HIT"


@pytest.mark.skipif(
    not hasattr(sessions, "Session"), reason="Starlette before 1.0 flags no writes"
)
def test_a_stored_answer_to_a_session_a_dependency_wrote_is_private() -> None:
    """#653: the middleware adds the new visitor's cookie to a HIT and a 304 too."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    async def visitor(request: Request) -> str:
        return str(request.session.setdefault("vid", secrets.token_hex(8)))

    @app.get("/page")
    @cache(ttl=60, public=True, debug_header=True)
    async def page(vid: Annotated[str, Depends(visitor)]) -> dict[str, str]:
        return {"page": "same for everyone"}

    returning = TestClient(app)
    returning.get("/page")  # writes the visitor ID: not stored
    stored = returning.get("/page")  # nothing written: stored
    assert stored.headers["Cache-Control"] == "public, max-age=60"

    first_time = TestClient(app).get("/page")
    revalidated = TestClient(app).get(
        "/page", headers={"If-None-Match": stored.headers["ETag"]}
    )

    for response in (first_time, revalidated):
        assert response.headers["X-Cache"] == "HIT"
        assert response.headers["Cache-Control"].startswith("private")
        assert "set-cookie" in response.headers
    assert revalidated.status_code == 304


def test_a_modified_flag_that_is_not_false_counts_as_written() -> None:
    """Only an explicit ``False`` is trusted from a custom session object."""

    class Flagged(dict[str, object]):
        modified = "yes"

    app = FastAPI()

    @app.middleware("http")
    async def middleware(request: Request, call_next):
        request.scope["session"] = Flagged()
        return await call_next(request)

    @app.get("/shared")
    @cache(ttl=60, public=True, debug_header=True)
    async def shared() -> dict[str, str]:
        return {"ok": "yes"}

    assert TestClient(app).get("/shared").headers["X-Cache-Reason"] == "set-cookie"
