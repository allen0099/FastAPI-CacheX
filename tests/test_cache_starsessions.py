"""`@cache` with starsessions, which loads the session only when asked (#679, #681).

Its middleware keeps a guard in ``request.session`` until ``load_session()``,
then a plain dict that records no writes, and it saves a loaded, non-empty
session and sends its cookie on every response.
"""

import secrets
import time
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Annotated
from typing import Any

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex import _stored_response as stored_response
from fastapi_cachex import build_cache_key
from fastapi_cachex import cache
from fastapi_cachex.proxy import BackendProxy

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


_Session = Annotated[dict[str, Any], Depends(_loaded)]


async def _csrf(session: _Session) -> str:
    return str(session.setdefault("csrf", secrets.token_hex(8)))


def _app() -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    calls = dict.fromkeys(
        ["plain", "maybe", "me", "form", "touch", "regen", "logout"], 0
    )

    @app.get("/login")
    async def login(session: _Session) -> dict[str, str]:
        session["user"] = "alice"
        return {}

    @app.get("/plain")
    @cache(ttl=60)
    async def plain() -> dict[str, int]:
        calls["plain"] += 1
        return {"n": calls["plain"]}

    @app.get("/maybe")
    @cache(ttl=60)
    async def maybe(request: Request) -> dict[str, object]:
        """Loads the session only for a request with its cookie."""
        calls["maybe"] += 1
        user = None
        if "session" in request.cookies:
            await starsessions.load_session(request)
            user = request.session.get("user")
        return {"user": user}

    @app.get("/logout")
    @cache(ttl=60)
    async def logout(request: Request) -> dict[str, object]:
        calls["logout"] += 1
        await starsessions.load_session(request)
        user = request.session.get("user")
        request.session.clear()
        return {"bye": user}

    _add_per_caller_routes(app, calls)
    app.add_middleware(
        starsessions.SessionMiddleware,
        store=starsessions.InMemoryStore(),
        cookie_https_only=False,
    )
    return app, calls


def _add_per_caller_routes(app: FastAPI, calls: dict[str, int]) -> None:
    """Routes keyed per session ID, with a session a dependency loads."""

    @app.get("/me")
    @cache(**_PER_CALLER)
    async def me(session: _Session) -> dict[str, object]:
        calls["me"] += 1
        return {"user": session.get("user"), "n": calls["me"]}

    @app.get("/form")
    @cache(**_PER_CALLER)
    async def form(token: Annotated[str, Depends(_csrf)]) -> dict[str, str]:
        calls["form"] += 1
        return {"csrf": token}

    @app.get("/touch")
    @cache(**_PER_CALLER)
    async def touch(session: _Session) -> dict[str, int]:
        calls["touch"] += 1
        session["seen"] = calls["touch"]
        return {"n": calls["touch"]}

    @app.get("/regen")
    @cache(**_PER_CALLER)
    async def regen(request: Request, session: _Session) -> dict[str, int]:
        calls["regen"] += 1
        starsessions.regenerate_session_id(request)
        return {"n": calls["regen"]}


def _logged_in(app: FastAPI) -> TestClient:
    client = TestClient(app)
    client.get("/login")
    return client


async def _stored(path: str) -> list[str]:
    return [key for key in await BackendProxy.get().get_all_keys() if path in key]


def test_a_route_that_does_not_load_the_session_is_cached() -> None:
    """It answered 500 on 0.7.3 (#679). A request with the session cookie
    still counts as carrying session data: the handler could load it."""
    app, calls = _app()
    alice = _logged_in(app)

    first = TestClient(app).get("/plain")
    second = TestClient(app).get("/plain")
    with_cookie = alice.get("/plain")

    assert first.status_code == second.status_code == with_cookie.status_code == 200
    assert second.headers["Cache-Control"] == "max-age=60"
    assert with_cookie.headers["Cache-Control"] == _PRIVATE
    assert calls["plain"] == 2  # once to fill the entry, once for alice


def test_a_session_loaded_only_for_some_requests_is_not_shared() -> None:
    """The anonymous entry is not served to a session holder (#679)."""
    app, calls = _app()
    alice = _logged_in(app)

    TestClient(app).get("/maybe")
    response = alice.get("/maybe")

    assert response.json() == {"user": "alice"}
    assert response.headers["Cache-Control"] == _PRIVATE
    assert calls["maybe"] == 2


async def test_a_session_the_handler_loads_and_clears_is_not_stored() -> None:
    """Empty after the handler, but read and deleted by it (#679)."""
    app, calls = _app()
    alice = _logged_in(app)

    response = alice.get("/logout")
    anonymous = TestClient(app).get("/logout")

    assert response.json() == {"bye": "alice"}
    assert response.headers["Cache-Control"] == _PRIVATE
    assert anonymous.json() == {"bye": None}
    assert calls["logout"] == 2
    assert await _stored("/logout") == []


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


async def _rotated(session: _Session) -> str:
    """A CSRF token made anew on every request, in an existing session too."""
    session["csrf"] = secrets.token_hex(8)
    return str(session["csrf"])


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"public": True}, id="public"),
        pytest.param(
            {"cache_authorized": True, "key_builder": lambda request: "shared"},
            id="key-shared-by-callers",
        ),
    ],
)
async def test_a_dependency_write_to_an_existing_session_is_not_stored(
    options: dict[str, Any],
) -> None:
    """Compared with the store, which still holds the session as the request
    found it (#681)."""
    app, _ = _app()
    calls = {"n": 0}

    @app.get("/rotate")
    @cache(ttl=60, **options)
    async def rotate(token: Annotated[str, Depends(_rotated)]) -> dict[str, str]:
        calls["n"] += 1
        return {"csrf": token}

    alice = _logged_in(app)
    bob = _logged_in(app)
    first = alice.get("/rotate")
    second = bob.get("/rotate")

    assert first.json() != second.json()
    assert calls["n"] == 2
    assert await _stored("/rotate") == []


def test_a_stale_entry_is_refreshed_for_an_unchanged_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh runs after starsessions saved the session and added its
    ``__metadata__``, which is not a change (#681)."""
    app, _ = _app()
    calls = {"n": 0}

    @app.get("/swr")
    @cache(
        ttl=1,
        serve_stale=True,
        stale="revalidate",
        stale_ttl=60,
        cache_authorized=True,
        key_builder=_per_session,
    )
    async def swr(session: _Session) -> dict[str, int]:
        calls["n"] += 1
        return {"n": calls["n"]}

    client = _logged_in(app)
    client.get("/swr")
    now = time.time() + 2
    monkeypatch.setattr(stored_response, "_now", lambda: now)
    stale = client.get("/swr")  # served stale, refreshed after it
    fresh = client.get("/swr")

    assert stale.json() == {"n": 1}
    assert fresh.json() == {"n": 2}


def test_a_new_session_a_dependency_writes_is_not_stored() -> None:
    """A CSRF token put into a new visitor's session stays theirs (#653)."""
    app, calls = _app()

    first = TestClient(app).get("/form")
    second = TestClient(app).get("/form")

    assert first.json() != second.json()
    assert first.headers["Cache-Control"] == _PRIVATE
    assert calls["form"] == 2


async def test_a_session_new_to_the_store_is_not_stored_under_a_sent_id() -> None:
    """A cookie with an ID the store does not know starts a new session: the
    dependency's write is the first data it has (#681)."""
    app, calls = _app()
    client = TestClient(app, cookies={"session": "unknown"})

    client.get("/form")

    assert calls["form"] == 1
    assert await _stored("/form") == []


@pytest.mark.parametrize("path", ["/touch", "/regen"])
async def test_a_session_the_handler_changes_is_not_stored(path: str) -> None:
    """A handler write, or a new session ID, is a real write (#681)."""
    app, calls = _app()
    client = _logged_in(app)

    response = client.get(path)

    assert response.headers["Cache-Control"] == _PRIVATE
    assert calls[path.lstrip("/")] == 1
    assert await _stored(path) == []


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


def test_a_session_that_fails_to_read_is_not_taken_for_no_session() -> None:
    """Only starsessions' own guard counts as not loaded (#679)."""

    class _Broken(dict[str, str]):
        @property
        def modified(self) -> bool:
            name = "modified"
            raise KeyError(name)

    app = FastAPI()

    @app.middleware("http")
    async def broken(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.scope["session"] = _Broken(user=request.headers["x-user"])
        return await call_next(request)

    @app.get("/me")
    @cache(ttl=60)
    async def me(request: Request) -> dict[str, str]:
        return {"user": request.session["user"]}

    client = TestClient(app, raise_server_exceptions=False)
    client.get("/me", headers={"x-user": "alice"})
    response = client.get("/me", headers={"x-user": "bob"})

    assert "alice" not in response.text  # a 500 as on 0.7.3, or bob's own
