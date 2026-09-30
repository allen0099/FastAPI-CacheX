"""``logout()``, ``login(keep=...)`` and the read-only ``Session.user`` (#256)."""

from typing import Annotated
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Query
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import Session
from fastapi_cachex.session import login
from fastapi_cachex.session import logout
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.exceptions import SessionNotFoundError
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.models import SessionUser

from .test_login import TRANSPORTS
from .test_login import _auth
from .test_login import _me
from .test_login import _token_sent


def _app(manager: SessionManager, config: SessionConfig) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.post("/logout")
    async def log_out(request: Request) -> dict[str, Any]:
        was_loaded = await logout(request)
        # The record is gone before the response, not when it is sent.
        keys = await manager.backend.get_all_keys()
        return {
            "logged_out": was_loaded,
            "keys": keys,
            "state": getattr(request.state, "__fastapi_cachex_session", "unset"),
            "dict": dict(request.session),
        }

    @app.post("/logout-then-write")
    async def logout_then_write(request: Request) -> dict[str, bool]:
        await logout(request)
        request.session["after"] = 1
        return {"ok": True}

    @app.post("/logout-then-login")
    async def logout_then_login(request: Request) -> dict[str, bool]:
        await logout(request)
        await login(request, SessionUser(user_id="bob"))
        return {"ok": True}

    @app.post("/login-keep")
    async def login_keep(
        request: Request,
        keep: Annotated[list[str], Query()] = [],  # noqa: B006
        name: str = "alice",
    ) -> dict[str, Any]:
        request.session["before"] = 1
        session = await login(request, SessionUser(user_id=name), keep=keep)
        # The record stored under the new ID already lacks the dropped keys.
        data_at_login = dict(session.data)
        request.session["after"] = 2
        return {"data_at_login": data_at_login}

    @app.post("/login-then-logout")
    async def login_then_logout(request: Request) -> dict[str, bool]:
        await login(request, SessionUser(user_id="alice"))
        return {"logged_out": await logout(request)}

    @app.get("/me")
    async def me(session: AuthenticatedSession) -> dict[str, Any]:
        assert session.user is not None
        return {"user": session.user.user_id, "data": session.data}

    return app


def _post(
    client: TestClient,
    path: str,
    transport: str,
    config: SessionConfig,
    token: str,
    **kwargs: Any,
) -> Any:
    client.cookies.clear()
    if transport == "cookie":
        client.cookies.set(config.cookie_name, token)
        return client.post(path, **kwargs)
    return client.post(path, headers=_auth(transport, config, token), **kwargs)


# --- logout() -------------------------------------------------------------------


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_logout_deletes_the_session_at_once(
    manager: SessionManager, config: SessionConfig, transport: str
) -> None:
    """The record is deleted during the call; the token no longer resolves."""
    _session, token = await manager.create_session(
        SessionUser(user_id="alice"), cart=["book"]
    )
    client = TestClient(_app(manager, config))

    response = _post(client, "/logout", transport, config, token)

    assert response.status_code == 200
    assert response.json() == {
        "logged_out": True,
        "keys": [],
        "state": None,
        "dict": {},
    }
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token)
    assert config.header_name.lower() not in response.headers
    if transport == "cookie":
        assert "expires=Thu, 01 Jan 1970" in response.headers["set-cookie"]
        assert response.headers["cache-control"] == "private, no-store"
    else:
        assert "set-cookie" not in response.headers
    assert _me(client, transport, config, token).status_code == 401


async def test_logout_without_a_session_sends_nothing(
    manager: SessionManager, config: SessionConfig
) -> None:
    response = TestClient(_app(manager, config)).post("/logout")

    assert response.json()["logged_out"] is False
    assert "set-cookie" not in response.headers


async def test_writes_after_logout_start_a_new_anonymous_session(
    manager: SessionManager, config: SessionConfig, backend: MemoryBackend
) -> None:
    session, token = await manager.create_session(
        SessionUser(user_id="alice"), cart=["book"]
    )
    client = TestClient(_app(manager, config))

    response = _post(client, "/logout-then-write", "cookie", config, token)

    new_session, _ = await manager.get_session(_token_sent(response, "cookie", config))
    assert new_session.session_id != session.session_id
    assert new_session.user is None
    assert new_session.data == {"after": 1}
    assert len(await backend.get_all_keys()) == 1


async def test_logout_then_login_starts_a_new_session(
    manager: SessionManager, config: SessionConfig, backend: MemoryBackend
) -> None:
    session, token = await manager.create_session(
        SessionUser(user_id="alice"), cart=["book"]
    )
    client = TestClient(_app(manager, config))

    response = _post(client, "/logout-then-login", "cookie", config, token)

    new_session, _ = await manager.get_session(_token_sent(response, "cookie", config))
    assert new_session.session_id != session.session_id
    assert new_session.user is not None
    assert new_session.user.user_id == "bob"
    assert new_session.data == {}
    assert len(await backend.get_all_keys()) == 1


async def test_login_then_logout_deletes_the_new_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """The session login() started is the one logout() ends."""
    client = TestClient(_app(manager, config))

    response = client.post("/login-then-logout")

    assert response.json() == {"logged_out": True}
    assert await manager.backend.get_all_keys() == []
    assert "01 jan 1970" in response.headers["set-cookie"].lower()


async def test_logout_outside_the_middleware_raises() -> None:
    request = Request({"type": "http", "method": "POST", "headers": []})

    with pytest.raises(RuntimeError, match=r"logout\(\) needs"):
        await logout(request)


# --- login(keep=...) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("keep", "kept"),
    [
        (["cart"], {"cart": ["book"]}),
        (["cart", "before", "missing"], {"cart": ["book"], "before": 1}),
        ([], {}),
    ],
)
async def test_login_keep_narrows_the_carried_data(
    manager: SessionManager,
    config: SessionConfig,
    keep: list[str],
    kept: dict[str, Any],
) -> None:
    """Only the listed keys, stored or written before login(), are carried.

    The record stored under the new ID never holds a dropped key (``before``
    lives only in ``request.session`` until the response), and what the
    handler writes after login() is saved as usual.
    """
    _anonymous, token = await manager.create_anonymous_session(
        cart=["book"], tracking="planted"
    )
    client = TestClient(_app(manager, config))

    response = _post(
        client, "/login-keep", "cookie", config, token, params={"keep": keep}
    )

    stored_at_login = {k: v for k, v in kept.items() if k != "before"}
    assert response.json() == {"data_at_login": stored_at_login}
    session, _ = await manager.get_session(_token_sent(response, "cookie", config))
    assert session.data == {**kept, "after": 2}
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token)


async def test_login_keep_applies_to_a_relogin(
    manager: SessionManager, config: SessionConfig
) -> None:
    _session, token = await manager.create_session(
        SessionUser(user_id="alice"), cart=["book"], elevated=True
    )
    client = TestClient(_app(manager, config))

    response = _post(
        client, "/login-keep", "cookie", config, token, params={"keep": ["cart"]}
    )

    session, _ = await manager.get_session(_token_sent(response, "cookie", config))
    assert session.data == {"cart": ["book"], "after": 2}


@pytest.mark.parametrize(("keep", "kept"), [(["before"], {"before": 1}), ([], {})])
async def test_login_keep_applies_without_a_loaded_session(
    manager: SessionManager,
    config: SessionConfig,
    keep: list[str],
    kept: dict[str, Any],
) -> None:
    """A new visitor's writes before login() are narrowed the same way."""
    client = TestClient(_app(manager, config))

    response = client.post("/login-keep", params={"keep": keep})

    session, _ = await manager.get_session(_token_sent(response, "cookie", config))
    assert session.user is not None
    assert session.data == {**kept, "after": 2}


async def test_login_keep_never_carries_another_users_data(
    manager: SessionManager, config: SessionConfig
) -> None:
    """``keep`` narrows what is carried; it cannot bring back what is dropped."""
    _session, token = await manager.create_session(
        SessionUser(user_id="alice"), cart=["book"]
    )
    client = TestClient(_app(manager, config))

    response = _post(
        client,
        "/login-keep",
        "cookie",
        config,
        token,
        params={"keep": ["cart", "before"], "name": "bob"},
    )

    assert response.json() == {"data_at_login": {}}
    session, _ = await manager.get_session(_token_sent(response, "cookie", config))
    assert session.user is not None
    assert session.user.user_id == "bob"
    assert session.data == {"after": 2}


async def test_login_keep_rejects_a_string(
    manager: SessionManager, config: SessionConfig
) -> None:
    """``keep="cart"`` would keep the keys "c", "a", "r" and "t"."""
    request = Request({"type": "http", "method": "POST", "headers": []})

    with pytest.raises(TypeError, match=r"keep=\['cart'\]"):
        await login(request, SessionUser(user_id="alice"), keep="cart")


# --- Read-only Session.user -----------------------------------------------------


def test_session_user_cannot_be_assigned() -> None:
    session = Session()

    with pytest.raises(AttributeError, match=r"login\(request, user\)"):
        session.user = SessionUser(user_id="alice")

    assert session.user is None


def test_other_session_fields_stay_assignable() -> None:
    session = Session()

    session.data = {"cart": ["book"]}
    session.ip_address = "203.0.113.7"

    assert session.data == {"cart": ["book"]}
    assert session.ip_address == "203.0.113.7"


def test_a_user_can_still_be_given_when_a_session_is_built() -> None:
    """Construction and loading from the backend are not assignments."""
    session = Session(user=SessionUser(user_id="alice"))
    loaded = Session.model_validate_json(session.model_dump_json())

    assert loaded.user is not None
    assert loaded.user.user_id == "alice"
