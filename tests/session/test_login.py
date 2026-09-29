"""Tests for ``login()``: attaching a user through FastAPICacheXSessionMiddleware (#293)."""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import login
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.dependencies import OptionalSession
from fastapi_cachex.session.exceptions import SessionNotFoundError
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.models import SessionUser

TRANSPORTS = ["cookie", "header", "bearer"]


def _login_app(manager: SessionManager, config: SessionConfig) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.post("/cart")
    async def add_to_cart(request: Request) -> dict[str, bool]:
        request.session["cart"] = ["book"]
        return {"ok": True}

    @app.post("/login")
    async def log_in(request: Request, name: str = "alice") -> dict[str, Any]:
        session = await login(request, SessionUser(user_id=name))
        return {"session_id": session.session_id}

    @app.post("/write-then-login")
    async def write_then_login(request: Request, name: str) -> dict[str, Any]:
        request.session["before"] = 1
        session = await login(request, SessionUser(user_id=name, roles=["fresh"]))
        request.session["after"] = 2
        return {"session_id": session.session_id}

    @app.post("/login-then-write")
    async def login_then_write(request: Request) -> dict[str, bool]:
        request.session["before"] = 1
        await login(request, SessionUser(user_id="alice"))
        request.session["after"] = 2
        return {"ok": True}

    @app.post("/login-then-clear")
    async def login_then_clear(request: Request) -> dict[str, bool]:
        await login(request, SessionUser(user_id="alice"))
        request.session.clear()
        return {"ok": True}

    @app.post("/clear-then-login")
    async def clear_then_login(request: Request) -> dict[str, bool]:
        request.session.clear()
        await login(request, SessionUser(user_id="alice"))
        return {"ok": True}

    @app.post("/login-twice")
    async def login_twice(request: Request) -> dict[str, bool]:
        await login(request, SessionUser(user_id="alice"))
        await login(request, SessionUser(user_id="bob"))
        return {"ok": True}

    @app.post("/login-and-read")
    async def login_and_read(request: Request) -> dict[str, Any]:
        session = await login(request, SessionUser(user_id="alice"))
        # What the session dependencies see for the rest of the request.
        seen = getattr(request.state, "__fastapi_cachex_session", None)
        return {"same": seen is session}

    @app.get("/me")
    async def me(session: AuthenticatedSession) -> dict[str, Any]:
        assert session.user is not None
        return {"user": session.user.user_id, "data": session.data}

    @app.get("/whoami")
    async def whoami(session: OptionalSession) -> dict[str, Any]:
        return {"session_id": session.session_id if session else None}

    return app


def _auth(transport: str, config: SessionConfig, token: str) -> dict[str, str]:
    """Request headers that carry ``token`` over a header transport."""
    if transport == "header":
        return {config.header_name: token}
    return {"Authorization": f"Bearer {token}"}


def _token_sent(response: Any, transport: str, config: SessionConfig) -> str:
    """The token the login response sent, asserting it used ``transport``."""
    if transport == "cookie":
        assert config.header_name.lower() not in response.headers
        value = response.cookies.get(config.cookie_name)
    else:
        assert "set-cookie" not in response.headers
        value = response.headers.get(config.header_name)
    assert value
    return str(value)


def _me(client: TestClient, transport: str, config: SessionConfig, token: str) -> Any:
    """GET /me carrying ``token`` over ``transport`` and nothing else."""
    client.cookies.clear()
    if transport == "cookie":
        client.cookies.set(config.cookie_name, token)
        return client.get("/me")
    return client.get("/me", headers=_auth(transport, config, token))


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_new_visitor_logs_in(
    manager: SessionManager, config: SessionConfig, transport: str
) -> None:
    """With no session loaded, login() starts one and the next request is a user.

    A header or Bearer client counts as new when its token no longer resolves;
    one that sent no token at all is answered with the cookie.
    """
    client = TestClient(_login_app(manager, config))
    headers = {} if transport == "cookie" else _auth(transport, config, "stale")

    response = client.post("/login", headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    token = _token_sent(response, transport, config)
    session, _ = await manager.get_session(token)
    assert session.session_id == response.json()["session_id"]
    assert session.user is not None
    assert session.user.user_id == "alice"

    me = _me(client, transport, config, token)
    assert me.status_code == 200
    assert me.json() == {"user": "alice", "data": {}}


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_anonymous_session_keeps_its_data_under_a_new_id(
    manager: SessionManager, config: SessionConfig, transport: str
) -> None:
    """Logging in a loaded session rotates its ID; the old token stops working."""
    anonymous, old_token = await manager.create_anonymous_session(cart=["book"])
    client = TestClient(_login_app(manager, config))
    if transport == "cookie":
        client.cookies.set(config.cookie_name, old_token)
        response = client.post("/login")
    else:
        response = client.post("/login", headers=_auth(transport, config, old_token))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    token = _token_sent(response, transport, config)
    assert token != old_token
    assert response.json()["session_id"] != anonymous.session_id
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(old_token)

    me = _me(client, transport, config, token)
    assert me.status_code == 200
    assert me.json() == {"user": "alice", "data": {"cart": ["book"]}}
    assert _me(client, transport, config, old_token).status_code == 401


def test_tokenless_request_gets_only_the_cookie(
    manager: SessionManager, config: SessionConfig
) -> None:
    """A request with no token is answered as a cookie client, as for any new session."""
    response = TestClient(_login_app(manager, config)).post("/login")

    assert config.cookie_name in response.cookies
    assert config.header_name.lower() not in response.headers


def test_cart_then_login_over_cookies(
    manager: SessionManager, config: SessionConfig
) -> None:
    """The browser flow: an anonymous cart, then login, then a user route."""
    client = TestClient(_login_app(manager, config))
    client.post("/cart")
    assert client.get("/me").status_code == 401

    assert client.post("/login").status_code == 200

    assert client.get("/me").json() == {"user": "alice", "data": {"cart": ["book"]}}


async def test_writes_around_login_are_saved(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Keys written to request.session before and after login() are both kept."""
    client = TestClient(_login_app(manager, config))

    response = client.post("/login-then-write")

    token = _token_sent(response, "cookie", config)
    session, _ = await manager.get_session(token)
    assert session.data == {"before": 1, "after": 2}
    assert session.user is not None


async def test_login_then_clear_logs_out(
    manager: SessionManager, config: SessionConfig, backend: MemoryBackend
) -> None:
    """clear() after login() in the same request is a logout: nothing survives."""
    client = TestClient(_login_app(manager, config))
    client.post("/cart")
    old_token = client.cookies[config.cookie_name]

    response = client.post("/login-then-clear")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert "expires=Thu, 01 Jan 1970" in response.headers["set-cookie"]
    assert not client.cookies.get(config.cookie_name)
    assert await backend.get_all_keys() == []
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(old_token)
    assert client.get("/me").status_code == 401


async def test_login_then_clear_over_the_header_sends_no_token(
    manager: SessionManager, config: SessionConfig, backend: MemoryBackend
) -> None:
    """A header client logged out in the same request gets no token at all."""
    client = TestClient(_login_app(manager, config))

    response = client.post("/login-then-clear", headers={config.header_name: "stale"})

    assert config.header_name.lower() not in response.headers
    assert "set-cookie" not in response.headers
    assert await backend.get_all_keys() == []


async def test_clear_then_login_starts_a_new_session(
    manager: SessionManager, config: SessionConfig, backend: MemoryBackend
) -> None:
    """clear() before login() logs the loaded session out; login() starts afresh."""
    anonymous, old_token = await manager.create_anonymous_session(cart=["book"])
    client = TestClient(_login_app(manager, config))
    client.cookies.set(config.cookie_name, old_token)

    response = client.post("/clear-then-login")

    token = _token_sent(response, "cookie", config)
    session, _ = await manager.get_session(token)
    assert session.session_id != anonymous.session_id
    assert session.user is not None
    assert session.data == {}
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(old_token)
    # Only the new session is left in the backend.
    assert len(await backend.get_all_keys()) == 1


async def test_clear_then_login_for_a_new_visitor(
    manager: SessionManager, config: SessionConfig
) -> None:
    """clear() with nothing loaded has nothing to log out; login() still works."""
    client = TestClient(_login_app(manager, config))

    response = client.post("/clear-then-login")

    token = _token_sent(response, "cookie", config)
    session, _ = await manager.get_session(token)
    assert session.user is not None
    assert client.get("/me").status_code == 200


async def test_login_twice_keeps_the_last_user(
    manager: SessionManager, config: SessionConfig, backend: MemoryBackend
) -> None:
    client = TestClient(_login_app(manager, config))

    response = client.post("/login-twice")

    token = _token_sent(response, "cookie", config)
    session, _ = await manager.get_session(token)
    assert session.user is not None
    assert session.user.user_id == "bob"
    assert len(await backend.get_all_keys()) == 1


def test_login_updates_the_request_session_for_dependencies(
    manager: SessionManager, config: SessionConfig
) -> None:
    """For the rest of the request, get_session returns the logged-in session."""
    client = TestClient(_login_app(manager, config))

    assert client.post("/login-and-read").json() == {"same": True}


async def test_new_session_is_bound_like_the_middlewares(
    backend: MemoryBackend,
) -> None:
    """A session login() creates gets the IP and User-Agent bindings."""
    config = SessionConfig(
        secret_key="a" * 32,
        ip_binding=True,
        user_agent_binding=True,
        cookie_name="session",
        cookie_https_only=False,
    )
    manager = SessionManager(backend, config)
    client = TestClient(_login_app(manager, config))

    response = client.post("/login", headers={"User-Agent": "browser/1"})

    token = _token_sent(response, "cookie", config)
    session, _ = await manager.get_session(
        token, ip_address="testclient", user_agent="browser/1"
    )
    assert session.ip_address == "testclient"
    assert session.user_agent == "browser/1"


async def test_login_outside_the_middleware_raises() -> None:
    """Without FastAPICacheXSessionMiddleware nobody would send the token."""
    request = Request({"type": "http", "method": "POST", "headers": []})

    with pytest.raises(RuntimeError, match="FastAPICacheXSessionMiddleware"):
        await login(request, SessionUser(user_id="alice"))


async def test_login_as_another_user_starts_a_clean_session(
    manager: SessionManager, config: SessionConfig, backend: MemoryBackend
) -> None:
    """A's session and anything written before login() never reach B."""
    session_a, token_a = await manager.create_session(
        SessionUser(user_id="alice"), cart=["book"], elevated=True
    )
    client = TestClient(_login_app(manager, config))
    client.cookies.set(config.cookie_name, token_a)

    response = client.post("/write-then-login", params={"name": "bob"})

    token_b = _token_sent(response, "cookie", config)
    session_b, _ = await manager.get_session(token_b)
    assert session_b.session_id != session_a.session_id
    assert session_b.user is not None
    assert session_b.user.user_id == "bob"
    assert session_b.data == {"after": 2}
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token_a)
    assert len(await backend.get_all_keys()) == 1
    assert _me(client, "cookie", config, token_b).json() == {
        "user": "bob",
        "data": {"after": 2},
    }


async def test_login_as_another_user_over_the_header(
    manager: SessionManager, config: SessionConfig
) -> None:
    _session_a, token_a = await manager.create_session(
        SessionUser(user_id="alice"), cart=["book"]
    )
    client = TestClient(_login_app(manager, config))

    response = client.post(
        "/write-then-login",
        params={"name": "bob"},
        headers={config.header_name: token_a},
    )

    token_b = _token_sent(response, "header", config)
    session_b, _ = await manager.get_session(token_b)
    assert session_b.data == {"after": 2}
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token_a)


async def test_relogin_keeps_the_data_and_updates_the_user(
    manager: SessionManager, config: SessionConfig, backend: MemoryBackend
) -> None:
    """The same user_id logging in again keeps the data; the new SessionUser wins."""
    session_a, token_a = await manager.create_session(
        SessionUser(user_id="alice", roles=["stale"]), cart=["book"]
    )
    client = TestClient(_login_app(manager, config))
    client.cookies.set(config.cookie_name, token_a)

    response = client.post("/write-then-login", params={"name": "alice"})

    token = _token_sent(response, "cookie", config)
    session, _ = await manager.get_session(token)
    assert session.session_id != session_a.session_id
    assert session.user is not None
    assert session.user.roles == ["fresh"]
    assert session.data == {"cart": ["book"], "before": 1, "after": 2}
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token_a)
    assert len(await backend.get_all_keys()) == 1


async def test_anonymous_login_keeps_writes_made_before_login(
    manager: SessionManager, config: SessionConfig
) -> None:
    """The cart case: an anonymous session's data and earlier writes are kept."""
    _anonymous, token = await manager.create_anonymous_session(cart=["book"])
    client = TestClient(_login_app(manager, config))
    client.cookies.set(config.cookie_name, token)

    response = client.post("/write-then-login", params={"name": "bob"})

    session, _ = await manager.get_session(_token_sent(response, "cookie", config))
    assert session.data == {"cart": ["book"], "before": 1, "after": 2}
