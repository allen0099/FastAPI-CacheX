"""Tests for the Starlette-aligned, backend-backed session middleware.

FastAPICacheXSessionMiddleware reuses starlette.middleware.sessions.Session for
its dict-like scope["session"] interface. That module imports itsdangerous at
module level, which is why itsdangerous is a base dependency rather than an
extra: the middleware cannot be constructed without it.
"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import get_session
from fastapi_cachex.session.exceptions import SessionNotFoundError
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.middleware import SessionMiddleware
from fastapi_cachex.session.models import SessionUser


@pytest.fixture
def config() -> SessionConfig:
    """Create session config for testing."""
    return SessionConfig(secret_key="a" * 32)


@pytest.fixture
def manager(config: SessionConfig) -> SessionManager:
    """Create session manager for testing."""
    backend = MemoryBackend()
    return SessionManager(backend, config)


def _extract_cookie_token(set_cookie_header: str, cookie_name: str) -> str:
    """Pull the cookie value out of a raw Set-Cookie header string."""
    first_pair = set_cookie_header.split(";", maxsplit=1)[0]
    name, _, value = first_pair.partition("=")
    assert name == cookie_name
    return value


def test_middleware_initialization(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test middleware initialization."""

    async def app(scope, receive, send):
        pass

    middleware = FastAPICacheXSessionMiddleware(app, manager, config)

    assert middleware.session_manager is manager
    assert middleware.config is config


def test_middleware_initialization_uses_manager_config(
    manager: SessionManager,
) -> None:
    """Ensure middleware defaults to manager config when none provided."""

    async def app(scope, receive, send):
        pass

    middleware = FastAPICacheXSessionMiddleware(app, manager)

    assert middleware.config is manager.config


def test_default_cookie_config_values() -> None:
    """Ensure cookie defaults mirror Starlette's own SessionMiddleware defaults."""
    config = SessionConfig(secret_key="a" * 32)

    assert config.cookie_name == "session"
    assert config.cookie_max_age == 14 * 24 * 60 * 60
    assert config.cookie_path == "/"
    assert config.cookie_same_site == "lax"
    assert config.cookie_https_only is False
    assert config.cookie_domain is None


def test_set_cookie_header_includes_secure_and_domain_flags(
    manager: SessionManager,
) -> None:
    """cookie_https_only and cookie_domain must be reflected in Set-Cookie."""
    config = SessionConfig(
        secret_key="a" * 32,
        cookie_https_only=True,
        cookie_domain="example.com",
    )
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/set")
    async def set_route(request: Request) -> dict[str, bool]:
        request.session["foo"] = "bar"
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/set")

    set_cookie = response.headers["set-cookie"]
    assert "secure" in set_cookie
    assert "domain=example.com" in set_cookie


@pytest.mark.asyncio
async def test_call_passes_through_non_http_scope() -> None:
    """Non-http/websocket scopes (e.g. lifespan) must bypass session handling entirely."""
    config = SessionConfig(secret_key="a" * 32)
    manager = SessionManager(MemoryBackend(), config)

    calls: list[str] = []

    async def app(scope, receive, send) -> None:
        calls.append(scope["type"])

    middleware = FastAPICacheXSessionMiddleware(app, manager, config)

    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type]

    assert calls == ["lifespan"]


def test_get_session_manager_di_stashed_once_across_requests(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Session manager is only stashed on app.state on the first request, not re-stashed."""
    from fastapi_cachex.session.dependencies import SessionManagerDep

    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/manager")
    async def manager_route(mgr: SessionManagerDep) -> dict[str, bool]:
        return {"is_same": mgr is manager}

    client = TestClient(app)
    first = client.get("/manager")
    second = client.get("/manager")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"is_same": True}
    assert second.json() == {"is_same": True}


def test_no_cookie_dict_untouched_no_set_cookie(
    manager: SessionManager, config: SessionConfig
) -> None:
    """No cookie + handler never touches request.session -> no Set-Cookie."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/test")
    async def test_route() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_no_cookie_dict_mutated_creates_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """No cookie + handler writes request.session -> new session persisted."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/set")
    async def set_route(request: Request) -> dict[str, bool]:
        request.session["foo"] = "bar"
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/set")

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    token = _extract_cookie_token(set_cookie, config.cookie_name)

    session, _ = await manager.get_session(token)
    assert session.data == {"foo": "bar"}


def test_no_cookie_dict_mutated_then_cleared_no_set_cookie(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Mutate-then-clear within one request, starting empty -> no Set-Cookie at all.

    Matches Starlette's own parity edge case: the "clear" branch is gated on
    "not initial_session_was_empty", which is False here since there was no
    session to begin with.
    """
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/roundtrip")
    async def roundtrip_route(request: Request) -> dict[str, bool]:
        request.session["foo"] = "bar"
        del request.session["foo"]
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/roundtrip")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_valid_cookie_dict_mutated_merges_data(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Existing session + mutation -> merged data persisted and re-sent."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/add")
    async def add_route(request: Request) -> dict[str, bool]:
        request.session["b"] = 2
        return {"ok": True}

    user = SessionUser(user_id="test-user")
    _session, token = await manager.create_session(user=user, a=1)

    client = TestClient(app)
    client.cookies.set(config.cookie_name, token)
    response = client.get("/add")

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    new_token = _extract_cookie_token(set_cookie, config.cookie_name)

    reloaded, _ = await manager.get_session(new_token)
    assert reloaded.data == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_valid_cookie_sliding_expiration_refreshes_cookie_without_mutation(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Sliding expiration renewal must refresh Set-Cookie even without a data change."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/noop")
    async def noop_route() -> dict[str, bool]:
        return {"ok": True}

    user = SessionUser(user_id="slide-user")
    session, token = await manager.create_session(user=user)

    # Shorten expiry so time_remaining < sliding threshold (< 50% of session_ttl)
    shortened_expiry = datetime.now(timezone.utc) + timedelta(seconds=100)
    session.expires_at = shortened_expiry
    await manager._save_session(session)

    client = TestClient(app)
    client.cookies.set(config.cookie_name, token)
    response = client.get("/noop")

    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie")
    # A Set-Cookie must be emitted purely because of the sliding renewal, even
    # though the route never touched request.session. Note: for the "simple"
    # token format the renewed token can be textually identical to the
    # original if generated within the same wall-clock second (session_id and
    # signature are unchanged, only the embedded issued_at second may differ),
    # so this doesn't assert the token string actually changed.
    assert set_cookie is not None
    new_token = _extract_cookie_token(set_cookie, config.cookie_name)

    renewed, _ = await manager.get_session(new_token)
    assert renewed.expires_at is not None
    assert renewed.expires_at > shortened_expiry


@pytest.mark.asyncio
async def test_valid_cookie_cleared_deletes_backend_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Clearing a non-empty session must delete the backend record and expire the cookie."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/clear")
    async def clear_route(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"ok": True}

    user = SessionUser(user_id="test-user")
    _session, token = await manager.create_session(user=user, x=1)

    client = TestClient(app)
    client.cookies.set(config.cookie_name, token)
    response = client.get("/clear")

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert f"{config.cookie_name}=;" in set_cookie
    assert "1970" in set_cookie

    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token)


def test_invalid_cookie_starts_fresh_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """A garbage/invalid token must not leak errors and behaves like no cookie at all."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/set")
    async def set_route(request: Request) -> dict[str, bool]:
        request.session["k"] = "v"
        return {"ok": True}

    client = TestClient(app)
    client.cookies.set(config.cookie_name, "not-a-real-token")
    response = client.get("/set")

    assert response.status_code == 200
    # A brand-new session must be created; the bad token is discarded entirely.
    assert "set-cookie" in response.headers


@pytest.mark.asyncio
async def test_expired_cookie_starts_fresh_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """An expired session token must be discarded, not surfaced as an error."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/test")
    async def test_route(request: Request) -> dict[str, bool]:
        return {"has_data": bool(dict(request.session))}

    user = SessionUser(user_id="test-user")
    session, token = await manager.create_session(user=user)
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await manager._save_session(session)

    client = TestClient(app)
    client.cookies.set(config.cookie_name, token)
    response = client.get("/test")

    assert response.status_code == 200
    assert response.json() == {"has_data": False}


@pytest.mark.asyncio
async def test_ip_binding_mismatch_starts_fresh_session(
    config: SessionConfig,
) -> None:
    """IP binding mismatch behaves like an invalid token: fresh empty session, not an HTTP error."""
    backend = MemoryBackend()
    manager = SessionManager(backend, config)
    config.ip_binding = True

    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/test")
    async def test_route(request: Request) -> dict[str, bool]:
        return {"has_data": bool(dict(request.session))}

    user = SessionUser(user_id="test-user")
    _session, token = await manager.create_session(user=user, ip_address="10.0.0.1")

    client = TestClient(app)
    client.cookies.set(config.cookie_name, token)
    # TestClient's default client IP won't match "10.0.0.1".
    response = client.get("/test")

    assert response.status_code == 200
    assert response.json() == {"has_data": False}


def test_get_client_ip_from_x_forwarded_for(config: SessionConfig) -> None:
    """Shared _get_client_ip reads X-Forwarded-For behind a trusted proxy."""
    from starlette.requests import HTTPConnection

    from fastapi_cachex.session.middleware import get_client_ip

    trusting = config.model_copy(update={"trusted_proxies": ["10.0.0.9", "10.0.0.1"]})
    scope: dict[str, Any] = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"192.168.1.1, 10.0.0.1")],
        "client": ("10.0.0.9", 12345),
    }
    connection = HTTPConnection(scope)

    assert get_client_ip(connection, trusting) == "192.168.1.1"


def test_get_client_ip_takes_the_rightmost_untrusted_entry(
    config: SessionConfig,
) -> None:
    """A caller-supplied entry sits to the left of the address the proxy added."""
    from starlette.requests import HTTPConnection

    from fastapi_cachex.session.middleware import get_client_ip

    trusting = config.model_copy(update={"trusted_proxies": ["10.0.0.9"]})
    scope: dict[str, Any] = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"198.51.100.5, 203.0.113.99")],
        "client": ("10.0.0.9", 12345),
    }
    connection = HTTPConnection(scope)

    assert get_client_ip(connection, trusting) == "203.0.113.99"


def test_get_client_ip_from_real_ip(config: SessionConfig) -> None:
    """Shared _get_client_ip falls back to X-Real-IP behind a trusted proxy."""
    from starlette.requests import HTTPConnection

    from fastapi_cachex.session.middleware import get_client_ip

    trusting = config.model_copy(update={"trusted_proxies": ["10.0.0.9"]})
    scope: dict[str, Any] = {
        "type": "http",
        "headers": [(b"x-real-ip", b"192.168.1.1")],
        "client": ("10.0.0.9", 12345),
    }
    connection = HTTPConnection(scope)

    assert get_client_ip(connection, trusting) == "192.168.1.1"


def test_get_client_ip_ignores_forwarded_headers_from_untrusted_peer(
    config: SessionConfig,
) -> None:
    """With no trusted proxies the headers are ignored entirely."""
    from starlette.requests import HTTPConnection

    from fastapi_cachex.session.middleware import get_client_ip

    scope: dict[str, Any] = {
        "type": "http",
        "headers": [
            (b"x-forwarded-for", b"1.2.3.4"),
            (b"x-real-ip", b"5.6.7.8"),
        ],
        "client": ("10.0.0.9", 12345),
    }
    connection = HTTPConnection(scope)

    assert get_client_ip(connection, config) == "10.0.0.9"


def test_get_client_ip_from_client(config: SessionConfig) -> None:
    """Shared _get_client_ip free function falls back to the raw client address."""
    from starlette.requests import HTTPConnection

    from fastapi_cachex.session.middleware import get_client_ip

    scope: dict[str, Any] = {
        "type": "http",
        "headers": [],
        "client": ("192.168.1.1", 12345),
    }
    connection = HTTPConnection(scope)

    assert get_client_ip(connection, config) == "192.168.1.1"


def test_get_client_ip_none(config: SessionConfig) -> None:
    """Shared _get_client_ip free function returns None when nothing is available."""
    from starlette.requests import HTTPConnection

    from fastapi_cachex.session.middleware import get_client_ip

    scope: dict[str, Any] = {"type": "http", "headers": [], "client": None}
    connection = HTTPConnection(scope)

    assert get_client_ip(connection, config) is None


@pytest.mark.asyncio
async def test_get_session_dependency_works_under_starlette_middleware(
    manager: SessionManager, config: SessionConfig
) -> None:
    """get_session must resolve the loaded Session under FastAPICacheXSessionMiddleware.

    Guards the request.state.__fastapi_cachex_session write added to
    FastAPICacheXSessionMiddleware.__call__: without it, get_session (which reads
    that state key) would always 401, even with a valid session cookie.
    """
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/me")
    async def me_route(session=Depends(get_session)):
        return {"user_id": session.user.user_id}

    user = SessionUser(user_id="cookie-user")
    _session, token = await manager.create_session(user=user)

    client = TestClient(app)

    # Without the cookie, the dependency has nothing to resolve -> 401.
    unauthenticated = client.get("/me")
    assert unauthenticated.status_code == 401

    # With a valid session cookie, the dependency must resolve the Session.
    client.cookies.set(config.cookie_name, token)
    authenticated = client.get("/me")
    assert authenticated.status_code == 200
    assert authenticated.json() == {"user_id": "cookie-user"}


@pytest.mark.asyncio
async def test_header_token_takes_priority_under_starlette_middleware(
    manager: SessionManager, config: SessionConfig
) -> None:
    """FastAPICacheXSessionMiddleware must accept the X-Session-Token header first.

    Guards the header-first-then-cookie token resolution: a token supplied via
    the configured header (config.header_name) must authenticate even with no
    cookie, and must win over an invalid session cookie.
    """
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/me")
    async def me_route(session=Depends(get_session)):
        return {"user_id": session.user.user_id}

    user = SessionUser(user_id="header-user")
    _session, token = await manager.create_session(user=user)

    client = TestClient(app)

    # Token via header, no cookie -> resolves the Session.
    header_only = client.get("/me", headers={config.header_name: token})
    assert header_only.status_code == 200
    assert header_only.json() == {"user_id": "header-user"}

    # Header must take priority over a bogus cookie.
    client.cookies.set(config.cookie_name, "not-a-valid-token")
    header_wins = client.get("/me", headers={config.header_name: token})
    assert header_wins.status_code == 200
    assert header_wins.json() == {"user_id": "header-user"}


@pytest.mark.asyncio
async def test_header_source_renewal_uses_response_header_not_cookie(
    manager: SessionManager, config: SessionConfig
) -> None:
    """A header-sourced token that gets sliding-renewed must be echoed via the
    response header, never as a Set-Cookie."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/noop")
    async def noop_route() -> dict[str, bool]:
        return {"ok": True}

    user = SessionUser(user_id="slide-header-user")
    session, token = await manager.create_session(user=user)

    # Shorten expiry so time_remaining < sliding threshold (< 50% of session_ttl).
    shortened_expiry = datetime.now(timezone.utc) + timedelta(seconds=100)
    session.expires_at = shortened_expiry
    await manager._save_session(session)

    client = TestClient(app)
    response = client.get("/noop", headers={config.header_name: token})

    assert response.status_code == 200
    # Renewal must be delivered via the response header, and no cookie is set.
    assert "set-cookie" not in response.headers
    renewed_token = response.headers.get(config.header_name)
    assert renewed_token is not None

    renewed, _ = await manager.get_session(renewed_token)
    assert renewed.expires_at is not None
    assert renewed.expires_at > shortened_expiry


@pytest.mark.asyncio
async def test_header_source_modify_persists_without_cookie(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Modifying a header-sourced session persists the data but emits neither a
    Set-Cookie nor a redundant token header (the token is unchanged)."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/add")
    async def add_route(request: Request) -> dict[str, bool]:
        request.session["b"] = 2
        return {"ok": True}

    user = SessionUser(user_id="mod-header-user")
    _session, token = await manager.create_session(user=user, a=1)

    client = TestClient(app)
    response = client.get("/add", headers={config.header_name: token})

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    # Token is unchanged, so nothing needs to be handed back via the header.
    assert config.header_name not in response.headers

    # The data change is still persisted under the same token.
    reloaded, _ = await manager.get_session(token)
    assert reloaded.data == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_header_source_invalid_token_new_session_via_header(
    manager: SessionManager, config: SessionConfig
) -> None:
    """An invalid header token that leads to a new session must return the new
    token via the response header, not a Set-Cookie."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/set")
    async def set_route(request: Request) -> dict[str, bool]:
        request.session["k"] = "v"
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/set", headers={config.header_name: "not-a-real-token"})

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    new_token = response.headers.get(config.header_name)
    assert new_token is not None

    created, _ = await manager.get_session(new_token)
    assert created.data == {"k": "v"}


def test_session_middleware_construction_is_deprecated(
    manager: SessionManager, config: SessionConfig
) -> None:
    """SessionMiddleware is deprecated in favor of FastAPICacheXSessionMiddleware."""

    async def app(scope, receive, send):
        pass

    with pytest.warns(DeprecationWarning, match="FastAPICacheXSessionMiddleware"):
        SessionMiddleware(app, manager, config)


@pytest.mark.asyncio
async def test_header_source_cleared_session_is_deleted_without_a_cookie(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Clearing a header-sourced session deletes the record and sets no cookie.

    The cookie transport answers a clear with an expiring `Set-Cookie`; a
    header client has no cookie to expire and simply keeps a token that no
    longer resolves. Only the cookie half of that branch was covered.
    """
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/logout")
    async def logout_route(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"ok": True}

    _session, token = await manager.create_session(
        user=SessionUser(user_id="header-logout-user"), data={"seen": True}
    )

    client = TestClient(app)
    response = client.get("/logout", headers={config.header_name: token})

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token)


def _regenerating_app(
    manager: SessionManager,
    config: SessionConfig,
    *,
    write_data: bool,
    middleware: Any = FastAPICacheXSessionMiddleware,
) -> FastAPI:
    """An app whose /login regenerates the request's session ID, as docs advise."""
    app = FastAPI()
    app.add_middleware(middleware, session_manager=manager, config=config)

    @app.post("/login")
    async def login(request: Request, session=Depends(get_session)):
        await manager.regenerate_session_id(session)
        if write_data:
            request.session["logged_in"] = True
        return {"ok": True}

    return app


async def _shorten_expiry(manager: SessionManager, token: str) -> None:
    """Push the session under the sliding threshold so the load renews it."""
    session, _ = await manager.get_session(token)
    session.expires_at = datetime.now(timezone.utc) + timedelta(seconds=100)
    await manager._save_session(session)


@pytest.mark.parametrize("write_data", [True, False])
@pytest.mark.parametrize("sliding", [True, False])
@pytest.mark.asyncio
async def test_regenerated_session_id_is_sent_as_cookie(
    manager: SessionManager, config: SessionConfig, write_data: bool, sliding: bool
) -> None:
    """After regenerate_session_id() the cookie must carry a token for the new ID.

    The middleware used to re-send the loaded token, which named the deleted
    record, so logging in with the documented fixation defence logged the user
    straight back out.
    """
    _session, old_token = await manager.create_session(
        user=SessionUser(user_id="u"), a=1
    )
    if sliding:
        await _shorten_expiry(manager, old_token)
    client = TestClient(_regenerating_app(manager, config, write_data=write_data))
    client.cookies.set(config.cookie_name, old_token)

    response = client.post("/login")

    assert response.status_code == 200
    new_token = _extract_cookie_token(
        response.headers["set-cookie"], config.cookie_name
    )
    assert new_token != old_token
    session, _ = await manager.get_session(new_token)
    expected = {"a": 1, "logged_in": True} if write_data else {"a": 1}
    assert session.data == expected
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(old_token)


@pytest.mark.parametrize("write_data", [True, False])
@pytest.mark.parametrize("sliding", [True, False])
@pytest.mark.asyncio
async def test_regenerated_session_id_is_sent_in_the_header(
    manager: SessionManager, config: SessionConfig, write_data: bool, sliding: bool
) -> None:
    """A header client gets the new ID's token back in the response header."""
    _session, old_token = await manager.create_session(user=SessionUser(user_id="u"))
    if sliding:
        await _shorten_expiry(manager, old_token)
    client = TestClient(_regenerating_app(manager, config, write_data=write_data))

    response = client.post("/login", headers={config.header_name: old_token})

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    new_token = response.headers[config.header_name]
    assert new_token != old_token
    await manager.get_session(new_token)
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(old_token)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("sliding", [True, False])
@pytest.mark.asyncio
async def test_deprecated_middleware_sends_regenerated_token(
    manager: SessionManager, config: SessionConfig, sliding: bool
) -> None:
    """SessionMiddleware must not overwrite the new ID's token with a renewed old one."""
    _session, old_token = await manager.create_session(user=SessionUser(user_id="u"))
    if sliding:
        await _shorten_expiry(manager, old_token)
    client = TestClient(
        _regenerating_app(
            manager, config, write_data=False, middleware=SessionMiddleware
        )
    )

    response = client.post("/login", headers={config.header_name: old_token})

    assert response.status_code == 200
    new_token = response.headers[config.header_name]
    await manager.get_session(new_token)
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(old_token)
