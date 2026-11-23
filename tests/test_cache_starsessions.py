"""`@cache` with starsessions, which loads the session only when asked (#679, #681).

Its middleware keeps a guard in ``request.session`` until ``load_session()``,
then a plain dict that records no writes, and it saves a loaded, non-empty
session and sends its cookie on every response.
"""

import secrets
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex import build_cache_key
from fastapi_cachex import cache

starsessions = pytest.importorskip("starsessions")

_PRIVATE = "private, max-age=60"


def _per_session(request: Request) -> str:
    """Key on the session ID, as the docs suggest for a session cookie."""
    return build_cache_key(request, request.cookies.get("session", ""))


_PER_CALLER: dict[str, Any] = {
    "ttl": 60,
    "cache_authorized": True,
    "key_builder": _per_session,
}


async def _loaded(request: Request) -> dict[str, Any]:
    await starsessions.load_session(request)
    return request.session


def _app() -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    calls = {"plain": 0, "me": 0, "form": 0, "touch": 0, "regen": 0}

    async def csrf(session: dict[str, Any] = Depends(_loaded)) -> str:
        return str(session.setdefault("csrf", secrets.token_hex(8)))

    @app.get("/login")
    async def login(session: dict[str, Any] = Depends(_loaded)) -> dict[str, str]:
        session["user"] = "alice"
        return {}

    @app.get("/plain")
    @cache(ttl=60)
    async def plain() -> dict[str, int]:
        calls["plain"] += 1
        return {"n": calls["plain"]}

    @app.get("/me")
    @cache(**_PER_CALLER)
    async def me(session: dict[str, Any] = Depends(_loaded)) -> dict[str, object]:
        calls["me"] += 1
        return {"user": session.get("user"), "n": calls["me"]}

    @app.get("/form")
    @cache(**_PER_CALLER)
    async def form(token: str = Depends(csrf)) -> dict[str, str]:
        calls["form"] += 1
        return {"csrf": token}

    @app.get("/touch")
    @cache(**_PER_CALLER)
    async def touch(session: dict[str, Any] = Depends(_loaded)) -> dict[str, int]:
        calls["touch"] += 1
        session["seen"] = calls["touch"]
        return {"n": calls["touch"]}

    @app.get("/regen")
    @cache(**_PER_CALLER)
    async def regen(
        request: Request, session: dict[str, Any] = Depends(_loaded)
    ) -> dict[str, int]:
        calls["regen"] += 1
        starsessions.regenerate_session_id(request)
        return {"n": calls["regen"]}

    app.add_middleware(
        starsessions.SessionMiddleware,
        store=starsessions.InMemoryStore(),
        cookie_https_only=False,
    )
    return app, calls


def _logged_in(app: FastAPI) -> TestClient:
    client = TestClient(app)
    client.get("/login")
    return client


def test_a_route_that_does_not_load_the_session_is_cached() -> None:
    """It answered 500 on 0.7.3 (#679)."""
    app, calls = _app()
    client = _logged_in(app)

    first = client.get("/plain")
    second = client.get("/plain")

    assert first.status_code == second.status_code == 200
    assert second.headers["Cache-Control"] == "max-age=60"
    assert calls["plain"] == 1


def test_an_unchanged_session_is_stored_per_caller() -> None:
    """The cookie only carries the same session ID back (#681)."""
    app, calls = _app()
    alice = _logged_in(app)
    bob = _logged_in(app)

    alice.get("/me")
    response = alice.get("/me")
    bob.get("/me")

    assert response.json() == {"user": "alice", "n": 1}
    assert response.headers["Cache-Control"] == _PRIVATE
    assert "set-cookie" in response.headers
    assert calls["me"] == 2  # once for alice, once for bob


def test_a_new_session_a_dependency_writes_is_not_stored() -> None:
    """A CSRF token put into a new visitor's session stays theirs (#653)."""
    app, calls = _app()

    first = TestClient(app).get("/form")
    second = TestClient(app).get("/form")

    assert first.json() != second.json()
    assert first.headers["Cache-Control"] == _PRIVATE
    assert calls["form"] == 2


@pytest.mark.parametrize("path", ["/touch", "/regen"])
def test_a_session_the_handler_changes_is_not_stored(path: str) -> None:
    """A handler write, or a new session ID, is a real write (#681)."""
    app, calls = _app()
    client = _logged_in(app)

    client.get(path)
    response = client.get(path)

    assert response.json() == {"n": 2}
    assert response.headers["Cache-Control"] == _PRIVATE
    assert calls[path.lstrip("/")] == 2


def test_a_handler_that_only_looks_like_starsessions_is_not_trusted() -> None:
    """Another middleware's dict session with a lookalike handler (#681)."""

    class _Handler:
        is_loaded = True
        initially_empty = False
        session_id = "abc"

    app = FastAPI()
    calls = {"n": 0}

    @app.middleware("http")
    async def lookalike(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.scope["session"] = {"user": "alice"}
        request.scope["session_handler"] = _Handler()
        return await call_next(request)

    @app.get("/me")
    @cache(**_PER_CALLER)
    async def me() -> dict[str, int]:
        calls["n"] += 1
        return {"n": calls["n"]}

    client = TestClient(app, cookies={"session": "abc"})
    client.get("/me")
    client.get("/me")

    assert calls["n"] == 2
