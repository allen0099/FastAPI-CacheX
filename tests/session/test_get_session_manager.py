"""Tests for get_session_manager dependency."""

from typing import Annotated

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import SessionConfig
from fastapi_cachex.session import SessionManager
from fastapi_cachex.session import SessionMiddleware
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session import get_session_manager


@pytest.fixture
def config() -> SessionConfig:
    """Create session config for testing."""
    return SessionConfig(secret_key="a" * 32)


@pytest.fixture
def manager(config: SessionConfig) -> SessionManager:
    """Create session manager for testing."""
    backend = MemoryBackend()
    return SessionManager(backend, config)


def test_get_session_manager_dependency(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test get_session_manager dependency retrieves manager from app state."""
    app = FastAPI()

    # Add middleware which stores manager in app.state
    app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

    # Create endpoint that uses get_session_manager
    @app.get("/test")
    async def test_endpoint(
        session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ):
        return {"has_manager": session_manager is not None}

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert response.json()["has_manager"] is True


@pytest.mark.asyncio
async def test_get_session_manager_allows_create_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test using get_session_manager to create sessions."""
    app = FastAPI()

    # Add middleware
    app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

    # Create login endpoint
    @app.post("/login")
    async def login(
        username: str,
        session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ):
        user = SessionUser(user_id="123", username=username)
        session, token = await session_manager.create_session(user=user)
        user_id = session.user.user_id if session.user else None
        return {"token": token, "user_id": user_id}

    client = TestClient(app)
    response = client.post("/login?username=testuser")

    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data.get("user_id") == "123"


def test_get_session_manager_without_middleware_raises_error() -> None:
    """Test get_session_manager raises 500 if middleware not added."""
    app = FastAPI()

    # Don't add middleware - manager not in app.state

    @app.get("/test")
    async def test_endpoint(
        session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ):
        return {"manager": "ok"}

    client = TestClient(app)
    response = client.get("/test")

    # Should return 500 Internal Server Error
    assert response.status_code == 500
    assert "SessionManager not initialized" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_session_manager_full_workflow(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test complete workflow: create, get, delete session using dependency."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

    @app.post("/login")
    async def login(
        username: str,
        session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ):
        user = SessionUser(user_id="user123", username=username)
        _, token = await session_manager.create_session(user=user)
        return {"token": token}

    @app.post("/logout")
    async def logout(
        session_id: str,
        session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    ):
        await session_manager.delete_session(session_id)
        return {"message": "logged out"}

    client = TestClient(app)

    # Login
    login_response = client.post("/login?username=testuser")
    assert login_response.status_code == 200
    token = login_response.json()["token"]

    # Extract session_id from token (format: session_id:signature)
    session_id = token.split(":")[0]

    # Logout
    logout_response = client.post(f"/logout?session_id={session_id}")
    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "logged out"


def test_session_manager_type_annotation(
    manager: SessionManager, config: SessionConfig
) -> None:
    """Test SessionManagerDep type annotation works."""
    from fastapi_cachex.session.dependencies import SessionManagerDep

    app = FastAPI()
    app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

    @app.get("/test")
    async def test_endpoint(session_manager: SessionManagerDep):
        return {"manager_type": type(session_manager).__name__}

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert response.json()["manager_type"] == "SessionManager"
