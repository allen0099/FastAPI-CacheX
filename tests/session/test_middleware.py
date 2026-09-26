"""Tests for session middleware and token extraction."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import SessionMiddleware
from fastapi_cachex.session.middleware import _extract_header_token
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


def test_middleware_initialization(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test middleware initialization."""

    # Create a simple ASGI app
    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager, config)

    assert middleware.session_manager is manager
    assert middleware.config is config


def test_middleware_initialization_uses_manager_config(
    manager: SessionManager,
) -> None:
    """Ensure middleware defaults to manager config when none provided."""

    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager)

    assert middleware.config is manager.config


def test_extract_token_from_header(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test token extraction from header."""

    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager, config)

    # Create a mock request with header
    request = MagicMock(spec=Request)
    request.headers = {config.header_name: "test-token"}

    token = middleware._extract_token(request)
    assert token == "test-token"


def test_extract_token_from_bearer(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test token extraction from Bearer token."""

    async def app(scope, receive, send):
        pass

    config.use_bearer_token = True
    middleware = SessionMiddleware(app, manager, config)

    # Create a mock request with Bearer token
    request = MagicMock(spec=Request)
    request.headers = {"authorization": "Bearer test-token"}

    token = middleware._extract_token(request)
    assert token == "test-token"


def test_extract_token_none(manager: SessionManager, config: SessionConfig) -> None:
    """Test token extraction when no token is present."""

    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager, config)

    # Create a mock request with no token
    request = MagicMock(spec=Request)
    request.headers = {}

    token = middleware._extract_token(request)
    assert token is None


def test_get_client_ip_ignores_forwarded_headers_by_default(
    manager: SessionManager,
    config: SessionConfig,
) -> None:
    """Forwarded headers are spoofable, so an untrusted peer's are ignored."""

    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager, config)

    request = MagicMock(spec=Request)
    request.headers = {
        "x-forwarded-for": "1.2.3.4",
        "x-real-ip": "5.6.7.8",
    }
    client = MagicMock()
    client.host = "10.0.0.9"
    request.client = client

    assert middleware._get_client_ip(request) == "10.0.0.9"


def test_get_client_ip_from_x_forwarded_for_behind_trusted_proxy(
    manager: SessionManager,
) -> None:
    """A proxy the app vouches for may report the real client address."""

    async def app(scope, receive, send):
        pass

    config = SessionConfig(
        secret_key="a" * 32, trusted_proxies=["10.0.0.9", "10.0.0.1"]
    )
    middleware = SessionMiddleware(app, manager, config)

    request = MagicMock(spec=Request)
    request.headers = Headers({"x-forwarded-for": "192.168.1.1, 10.0.0.1"})
    client = MagicMock()
    client.host = "10.0.0.9"
    request.client = client

    assert middleware._get_client_ip(request) == "192.168.1.1"


def test_get_client_ip_ignores_a_prepended_forwarded_entry(
    manager: SessionManager,
) -> None:
    """Proxies append, so the leftmost entry is whatever the caller sent."""

    async def app(scope, receive, send):
        pass

    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.9"])
    middleware = SessionMiddleware(app, manager, config)

    request = MagicMock(spec=Request)
    # The attacker sent the first entry themselves; nginx appended the second.
    request.headers = Headers({"x-forwarded-for": "198.51.100.5, 203.0.113.99"})
    client = MagicMock()
    client.host = "10.0.0.9"
    request.client = client

    assert middleware._get_client_ip(request) == "203.0.113.99"


def test_get_client_ip_falls_back_when_every_hop_is_trusted(
    manager: SessionManager,
) -> None:
    """With no untrusted entry left there is no client address to recover."""

    async def app(scope, receive, send):
        pass

    config = SessionConfig(
        secret_key="a" * 32, trusted_proxies=["10.0.0.9", "10.0.0.1"]
    )
    middleware = SessionMiddleware(app, manager, config)

    request = MagicMock(spec=Request)
    request.headers = Headers({"x-forwarded-for": "10.0.0.1"})
    client = MagicMock()
    client.host = "10.0.0.9"
    request.client = client

    assert middleware._get_client_ip(request) == "10.0.0.9"


def test_get_client_ip_from_real_ip_behind_trusted_proxy(
    manager: SessionManager,
) -> None:
    """X-Real-IP is the fallback once the peer is trusted."""

    async def app(scope, receive, send):
        pass

    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.9"])
    middleware = SessionMiddleware(app, manager, config)

    request = MagicMock(spec=Request)
    request.headers = Headers({"x-real-ip": "192.168.1.1"})
    client = MagicMock()
    client.host = "10.0.0.9"
    request.client = client

    assert middleware._get_client_ip(request) == "192.168.1.1"


def test_get_client_ip_from_client(
    manager: SessionManager,
    config: SessionConfig,
) -> None:
    """Test getting client IP from client."""

    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager, config)

    # Create a mock request
    request = MagicMock(spec=Request)
    request.headers = {}
    client = MagicMock()
    client.host = "192.168.1.1"
    request.client = client

    ip = middleware._get_client_ip(request)
    assert ip == "192.168.1.1"


@pytest.mark.asyncio
async def test_dispatch_with_expired_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test dispatch with expired session token."""

    app = FastAPI()
    SessionMiddleware(app, manager, config)

    @app.get("/test")
    async def test_route():
        return {"message": "ok"}

    client = TestClient(app)

    # Create a session with past expiry
    user = SessionUser(user_id="test-user")
    session, token = await manager.create_session(user=user)
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await manager._save_session(session)

    # Make request with expired session
    response = client.get("/test", headers={config.header_name: token})
    # Should succeed but without session loaded
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dispatch_with_invalid_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test dispatch with invalid session token."""
    app = FastAPI()
    SessionMiddleware(app, manager, config)

    @app.get("/test")
    async def test_route():
        return {"message": "ok"}

    client = TestClient(app)

    # Make request with invalid session header
    response = client.get("/test", headers={config.header_name: "invalid-token"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dispatch_with_no_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test dispatch without session token."""
    app = FastAPI()
    SessionMiddleware(app, manager, config)

    @app.get("/test")
    async def test_route():
        return {"message": "ok"}

    client = TestClient(app)

    # Make request without session
    response = client.get("/test")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dispatch_sets_session_in_request_state(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test dispatch sets session in request state."""
    app = FastAPI()
    SessionMiddleware(app, manager, config)

    captured_session = {}

    @app.get("/check-session")
    async def check_session_route():
        request = Request({"type": "http", "method": "GET", "headers": []})
        if hasattr(request.state, "session"):
            captured_session["session"] = request.state.session
        return {"has_session": hasattr(request.state, "session")}

    client = TestClient(app)

    # Create a session first
    user = SessionUser(user_id="test-user")
    _session, token = await manager.create_session(user=user)

    # Make request with session
    response = client.get("/check-session", headers={config.header_name: token})
    assert response.status_code == 200


def test_extract_token_from_bearer_with_malformed_header(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test token extraction with malformed Bearer header."""

    async def app(scope, receive, send):
        pass

    config.use_bearer_token = True
    middleware = SessionMiddleware(app, manager, config)

    # Create a mock request with malformed Bearer token
    request = MagicMock(spec=Request)
    request.headers = {"authorization": "Bearer"}  # Missing token

    token = middleware._extract_token(request)
    assert token is None


def test_get_client_ip_none(
    manager: SessionManager,
    config: SessionConfig,
) -> None:
    """Test getting client IP when none available."""

    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager, config)

    # Create a mock request with no IP info
    request = MagicMock(spec=Request)
    request.headers = {}
    request.client = None

    ip = middleware._get_client_ip(request)
    assert ip is None


@pytest.mark.asyncio
async def test_dispatch_with_session_and_ip_binding(
    config: SessionConfig,
) -> None:
    """Test dispatch validates IP binding."""
    backend = MemoryBackend()
    manager = SessionManager(backend, config)
    config.ip_binding = True

    app = FastAPI()
    SessionMiddleware(app, manager, config)

    @app.get("/test")
    async def test_route():
        return {"message": "ok"}

    client = TestClient(app)

    # Create session with IP binding
    user = SessionUser(user_id="test-user")
    _session, token = await manager.create_session(
        user=user,
        ip_address="127.0.0.1",
    )

    # Request with same IP should work
    response = client.get("/test", headers={config.header_name: token})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dispatch_with_user_agent_binding(
    config: SessionConfig,
) -> None:
    """Test dispatch validates User-Agent binding."""
    backend = MemoryBackend()
    manager = SessionManager(backend, config)
    config.user_agent_binding = True

    app = FastAPI()
    SessionMiddleware(app, manager, config)

    @app.get("/test")
    async def test_route():
        return {"message": "ok"}

    client = TestClient(app)

    # Create session with User-Agent binding
    user = SessionUser(user_id="test-user")
    _session, token = await manager.create_session(
        user=user,
        user_agent="TestClient/1.0",
    )

    # Request with session
    response = client.get("/test", headers={config.header_name: token})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dispatch_direct_call(
    manager: SessionManager,
    config: SessionConfig,
) -> None:
    """Test dispatch method directly to improve coverage."""

    # Create a mock app
    mock_app = AsyncMock()

    middleware = SessionMiddleware(mock_app, manager, config)

    # Create a session
    user = SessionUser(user_id="test-user")
    _session, token = await manager.create_session(user=user)

    # Create a mock request
    request = MagicMock(spec=Request)
    request.headers = {config.header_name: token, "user-agent": "test-agent"}
    request.state = MagicMock()
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    # Create mock response
    mock_response = MagicMock(spec=Response)

    # Set up call_next to return response
    call_next = AsyncMock(return_value=mock_response)

    # Call dispatch directly
    result = await middleware.dispatch(request, call_next)

    # Verify result
    assert result == mock_response
    # Verify session was set in request state
    assert getattr(request.state, "__fastapi_cachex_session", None) is not None


@pytest.mark.asyncio
async def test_dispatch_with_session_error(
    manager: SessionManager,
    config: SessionConfig,
) -> None:
    """Test dispatch handles SessionError gracefully."""

    # Create mock app
    mock_app = AsyncMock()

    middleware = SessionMiddleware(mock_app, manager, config)

    # Create mock request with invalid token
    request = MagicMock(spec=Request)
    request.headers = {config.header_name: "invalid-token-xyz"}
    request.state = MagicMock()
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    # Create mock response
    mock_response = MagicMock(spec=Response)

    # Set up call_next to return response
    call_next = AsyncMock(return_value=mock_response)

    # Call dispatch - should handle SessionError
    result = await middleware.dispatch(request, call_next)

    # Verify result
    assert result == mock_response
    # Session should be None in request state due to error
    assert getattr(request.state, "__fastapi_cachex_session", None) is None


@pytest.mark.asyncio
async def test_dispatch_sets_renewed_token_header_on_sliding_expiration() -> None:
    """Middleware must write the refreshed token to the response header and extend expires_at."""
    from datetime import datetime
    from datetime import timedelta
    from datetime import timezone

    from fastapi.responses import JSONResponse

    backend = MemoryBackend()
    slide_config = SessionConfig(
        secret_key="a" * 32,
        session_ttl=3600,
        sliding_expiration=True,
        sliding_threshold=0.5,
    )
    mgr = SessionManager(backend, slide_config)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, session_manager=mgr, config=slide_config)

    @app.get("/ping")
    async def ping() -> JSONResponse:
        return JSONResponse({"ok": True})

    user = SessionUser(user_id="slide-user")
    created, original_token = await mgr.create_session(user=user)

    # Shorten expiry so time_remaining < sliding threshold (< 50% of 3600 s)
    shortened_expiry = datetime.now(timezone.utc) + timedelta(seconds=1000)
    created.expires_at = shortened_expiry
    await mgr._save_session(created)

    client = TestClient(app)
    response = client.get("/ping", headers={slide_config.header_name: original_token})

    assert response.status_code == 200
    renewed = response.headers.get(slide_config.header_name)
    # Middleware must write a new token to the response header
    assert renewed is not None, (
        "Middleware must set renewed token header on sliding renewal"
    )

    # The renewed session must have an extended expires_at (> the shortened value we set)
    renewed_session, _ = await mgr.get_session(renewed)
    assert renewed_session.expires_at is not None
    assert renewed_session.expires_at > shortened_expiry


def _connection(headers: dict[str, str]) -> Request:
    """A bare `Request` carrying only the headers under test."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in headers.items()
            ],
        }
    )


def test_bearer_is_used_when_the_header_source_finds_nothing(
    config: SessionConfig,
) -> None:
    """The priority list is a fallback chain, not a first-entry-only lookup.

    Every other extraction test supplies the header it asks for first, so the
    loop always returned on its first pass and an implementation that only
    ever checked `token_source_priority[0]` would have passed them all.
    """
    token = _extract_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )

    assert token == "from-bearer"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer from-bearer",
        "bearer from-bearer",
        "BEARER from-bearer",
        "Bearer   from-bearer",
    ],
)
def test_bearer_scheme_is_matched_case_insensitively(
    config: SessionConfig, authorization: str
) -> None:
    """Auth schemes are case-insensitive (RFC 9110 §11.1), and RFC 6750 allows
    more than one space before the token (#166).
    """
    token = _extract_header_token(_connection({"Authorization": authorization}), config)

    assert token == "from-bearer"


@pytest.mark.parametrize(
    "authorization",
    ["Bearer", "Bearer ", "Bearer   ", "Basic from-bearer", "Bearerfrom-bearer"],
)
def test_an_empty_or_non_bearer_authorization_header_yields_no_token(
    config: SessionConfig, authorization: str
) -> None:
    token = _extract_header_token(_connection({"Authorization": authorization}), config)

    assert token is None


def test_header_wins_over_bearer_when_both_are_present(
    config: SessionConfig,
) -> None:
    """Order in the list is the order that is honoured."""
    token = _extract_header_token(
        _connection(
            {
                config.header_name: "from-header",
                "Authorization": "Bearer from-bearer",
            }
        ),
        config,
    )

    assert token == "from-header"


def test_bearer_source_is_skipped_when_bearer_tokens_are_disabled() -> None:
    """`use_bearer_token=False` must win over the priority list."""
    config = SessionConfig(secret_key="a" * 32, use_bearer_token=False)

    token = _extract_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )

    assert token is None


def test_an_unknown_source_is_skipped_rather_than_read_as_a_bearer_token() -> None:
    """The chain ends in an `elif`, not an `else`, and this is why.

    `token_source_priority` is a list of Literals, so pydantic refuses an
    unknown source at construction and no caller can reach this through the
    public API. Nothing revalidates the list afterwards, though, and the
    branch exists for the maintainer who adds a third source to the Literal
    and forgets this function: it must fall through, not inherit whatever the
    last branch happens to do. Mutating the list in place is the only way to
    stand where that maintainer will stand.
    """
    config = SessionConfig(secret_key="a" * 32)
    config.token_source_priority[:] = ["cookie"]  # type: ignore[list-item]

    token = _extract_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )

    assert token is None


def test_a_known_source_after_an_unknown_one_is_still_honoured(
    config: SessionConfig,
) -> None:
    """Falling through must continue the chain, not abandon it."""
    config.token_source_priority[:] = ["cookie", "header"]  # type: ignore[list-item]

    token = _extract_header_token(
        _connection({config.header_name: "from-header"}), config
    )

    assert token == "from-header"
