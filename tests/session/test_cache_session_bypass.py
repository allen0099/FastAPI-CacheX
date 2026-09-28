"""`@cache` must not share a response to a request that arrived with a session (#319).

Only ``Authorization`` bypassed the shared backend, so a session token sent as
the ``X-Session-Token`` header or the session cookie gave one user's cached
response to the next, and an anonymous session's cart to every visitor.
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
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.models import SessionUser
from fastapi_cachex.types import CACHE_KEY_SEPARATOR

_PRIVATE = "private, max-age=60"


def _key(path: str) -> str:
    """The key `default_key_builder` produces for a TestClient GET."""
    return f"GET|||testserver|||{path}|||"


def _app(
    manager: SessionManager, config: SessionConfig, **cache_kwargs: object
) -> FastAPI:
    """Routes that answer from the session, under the session middleware."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/whoami")
    @cache(ttl=60, **cache_kwargs)  # type: ignore[arg-type]
    async def whoami(session: AuthenticatedSession) -> dict[str, str]:
        assert session.user is not None
        return {"user": session.user.user_id}

    @app.get("/cart")
    @cache(ttl=60)
    async def cart(request: Request) -> dict[str, list[str]]:
        return {"cart": request.session.get("cart", [])}

    @app.get("/add")
    async def add(request: Request, item: str) -> dict[str, bool]:
        request.session["cart"] = [*request.session.get("cart", []), item]
        return {"ok": True}

    return app


async def _token(manager: SessionManager, user_id: str) -> str:
    _session, token = await manager.create_session(user=SessionUser(user_id=user_id))
    return token


async def test_issue_repro_session_header(
    manager: SessionManager, config: SessionConfig
) -> None:
    """The report in #319: bob must not get alice's /whoami."""
    client = TestClient(_app(manager, config))
    alice = {config.header_name: await _token(manager, "alice")}
    bob = {config.header_name: await _token(manager, "bob")}

    assert client.get("/whoami", headers=alice).json() == {"user": "alice"}
    response = client.get("/whoami", headers=bob)

    assert response.json() == {"user": "bob"}
    assert response.headers["Cache-Control"] == _PRIVATE
    assert await BackendProxy.get().get(_key("/whoami")) is None


async def test_session_cookie_bypasses_the_backend(
    manager: SessionManager, config: SessionConfig
) -> None:
    app = _app(manager, config)
    alice = TestClient(
        app, cookies={config.cookie_name: await _token(manager, "alice")}
    )
    bob = TestClient(app, cookies={config.cookie_name: await _token(manager, "bob")})

    assert alice.get("/whoami").json() == {"user": "alice"}
    response = bob.get("/whoami")

    assert response.json() == {"user": "bob"}
    assert response.headers["Cache-Control"] == _PRIVATE
    assert await BackendProxy.get().get(_key("/whoami")) is None


async def test_bearer_session_bypasses_the_backend(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Already covered by the `Authorization` rule; kept so it stays that way."""
    client = TestClient(_app(manager, config))
    alice = {"Authorization": f"Bearer {await _token(manager, 'alice')}"}
    bob = {"Authorization": f"Bearer {await _token(manager, 'bob')}"}

    client.get("/whoami", headers=alice)

    assert client.get("/whoami", headers=bob).json() == {"user": "bob"}


async def test_anonymous_session_is_not_shared(
    manager: SessionManager, config: SessionConfig
) -> None:
    """A cart in an anonymous session reaches neither bob nor a new visitor."""
    app = _app(manager, config)
    alice = TestClient(app)
    alice.get("/add", params={"item": "apple"})

    assert alice.get("/cart").json() == {"cart": ["apple"]}
    assert TestClient(app).get("/cart").json() == {"cart": []}
    bob = TestClient(app)
    bob.get("/add", params={"item": "pear"})
    assert bob.get("/cart").json() == {"cart": ["pear"]}


async def test_request_without_a_session_is_still_cached(
    manager: SessionManager, config: SessionConfig
) -> None:
    app = _app(manager, config)
    calls = {"n": 0}

    @app.get("/news")
    @cache(ttl=60)
    async def news() -> dict[str, int]:
        calls["n"] += 1
        return {"n": calls["n"]}

    client = TestClient(app)
    client.get("/news")
    response = client.get("/news")

    assert response.json() == {"n": 1}
    assert response.headers["Cache-Control"] == "max-age=60"


@pytest.mark.parametrize("transport", ["header", "cookie"])
async def test_token_that_resolves_to_no_session_does_not_bypass(
    manager: SessionManager, config: SessionConfig, transport: str
) -> None:
    """Random or expired tokens must not keep requests away from the cache."""
    app = _app(manager, config)
    calls = {"n": 0}

    @app.get("/news")
    @cache(ttl=60)
    async def news() -> dict[str, int]:
        calls["n"] += 1
        return {"n": calls["n"]}

    TestClient(app).get("/news")
    if transport == "header":
        response = TestClient(app).get("/news", headers={config.header_name: "bogus"})
    else:
        response = TestClient(app, cookies={config.cookie_name: "bogus"}).get("/news")

    assert response.json() == {"n": 1}
    assert calls["n"] == 1


async def test_public_route_is_shared_across_sessions(
    manager: SessionManager, config: SessionConfig
) -> None:
    """As for `Authorization`: `public` says the response suits every caller."""
    app = _app(manager, config)
    calls = {"n": 0}

    @app.get("/catalog")
    @cache(ttl=60, public=True)
    async def catalog() -> dict[str, int]:
        calls["n"] += 1
        return {"n": calls["n"]}

    client = TestClient(app)
    client.get("/catalog", headers={config.header_name: await _token(manager, "alice")})
    response = client.get(
        "/catalog", headers={config.header_name: await _token(manager, "bob")}
    )

    assert response.json() == {"n": 1}
    assert response.headers["Cache-Control"] == "public, max-age=60"


async def test_cache_authorized_caches_per_session_entries(
    manager: SessionManager, config: SessionConfig
) -> None:
    def per_user_key(request: Request) -> str:
        session = request.state.__fastapi_cachex_session
        return f"{request.url.path}{CACHE_KEY_SEPARATOR}{session.user.user_id}"

    client = TestClient(
        _app(manager, config, key_builder=per_user_key, cache_authorized=True)
    )
    alice = {config.header_name: await _token(manager, "alice")}
    bob = {config.header_name: await _token(manager, "bob")}

    client.get("/whoami", headers=alice)

    assert client.get("/whoami", headers=bob).json() == {"user": "bob"}
    assert await BackendProxy.get().get("/whoami|||alice") is not None
    assert await BackendProxy.get().get("/whoami|||bob") is not None


def test_bypass_is_logged(
    manager: SessionManager, config: SessionConfig, caplog: pytest.LogCaptureFixture
) -> None:
    app = _app(manager, config)
    client = TestClient(app)
    client.get("/add", params={"item": "apple"})

    with caplog.at_level(logging.DEBUG, logger="fastapi_cachex.cache"):
        client.get("/cart")

    assert "Session present; bypassing the backend for path=/cart" in caplog.text


def _starlette_app() -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    app.add_middleware(StarletteSessionMiddleware, secret_key="s" * 32)
    calls = {"n": 0}

    @app.get("/cart")
    @cache(ttl=60)
    async def cart(request: Request) -> dict[str, object]:
        calls["n"] += 1
        return {"cart": request.session.get("cart", []), "n": calls["n"]}

    @app.get("/add")
    async def add(request: Request, item: str) -> dict[str, bool]:
        request.session["cart"] = [item]
        return {"ok": True}

    return app, calls


async def test_starlette_session_with_data_bypasses_the_backend() -> None:
    """Any session middleware: a non-empty `request.session` is per visitor."""
    app, _ = _starlette_app()
    alice = TestClient(app)
    alice.get("/add", params={"item": "apple"})

    response = alice.get("/cart")

    assert response.json()["cart"] == ["apple"]
    assert response.headers["Cache-Control"] == _PRIVATE
    assert await BackendProxy.get().get(_key("/cart")) is None


def test_empty_starlette_session_is_still_cached() -> None:
    app, calls = _starlette_app()
    client = TestClient(app)

    client.get("/cart")
    client.get("/cart")

    assert calls["n"] == 1
