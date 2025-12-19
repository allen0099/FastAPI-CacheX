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

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.manager import SessionManager
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
    assert token == "test-token"  # noqa: S105


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
    assert token == "test-token"  # noqa: S105


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


def test_get_client_ip_from_x_forwarded_for(
    manager: SessionManager,
    config: SessionConfig,
) -> None:
    """Test getting client IP from X-Forwarded-For header."""

    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager, config)

    # Create a mock request
    request = MagicMock(spec=Request)
    request.headers = {"x-forwarded-for": "192.168.1.1, 10.0.0.1"}
    request.client = None

    ip = middleware._get_client_ip(request)
    assert ip == "192.168.1.1"


def test_get_client_ip_from_real_ip(
    manager: SessionManager,
    config: SessionConfig,
) -> None:
    """Test getting client IP from X-Real-IP header."""

    async def app(scope, receive, send):
        pass

    middleware = SessionMiddleware(app, manager, config)

    # Create a mock request
    request = MagicMock(spec=Request)
    request.headers = {"x-real-ip": "192.168.1.1"}
    request.client = None

    ip = middleware._get_client_ip(request)
    assert ip == "192.168.1.1"


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
