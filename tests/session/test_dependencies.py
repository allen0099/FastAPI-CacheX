from unittest.mock import MagicMock

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import get_optional_session
from fastapi_cachex.session.dependencies import get_session
from fastapi_cachex.session.dependencies import require_session
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import SessionMiddleware
from fastapi_cachex.session.models import SessionUser


class TestSessionDependencies:
    """Test session dependencies."""

    def test_get_session(self):
        """Test get_session dependency."""

        # Create a mock request with session
        request_with_session = MagicMock(spec=Request)
        mock_session = MagicMock()
        setattr(request_with_session.state, "__fastapi_cachex_session", mock_session)

        session = get_session(request_with_session)
        assert session == mock_session

        # Create a mock request without session
        request_without_session = MagicMock(spec=Request)
        setattr(request_without_session.state, "__fastapi_cachex_session", None)

        with pytest.raises(HTTPException) as exc_info:
            get_session(request_without_session)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Authentication required"

    def test_get_optional_session(self):
        """Test get_optional_session dependency."""

        # Create a mock request with session
        request_with_session = MagicMock(spec=Request)
        mock_session = MagicMock()
        setattr(request_with_session.state, "__fastapi_cachex_session", mock_session)

        session = get_optional_session(request_with_session)
        assert session == mock_session

        # Create a mock request without session
        request_without_session = MagicMock(spec=Request)
        setattr(request_without_session.state, "__fastapi_cachex_session", None)

        session = get_optional_session(request_without_session)
        assert session is None


class TestRequireSessionAlias:
    """Test the require_session alias behaves identically to get_session."""

    def test_require_session_is_get_session_alias(self) -> None:
        assert require_session is get_session

    def test_require_session_via_http_endpoint(self) -> None:
        """require_session used as a route dependency must return 401 without session."""
        config = SessionConfig(secret_key="a" * 32)
        backend = MemoryBackend()
        manager = SessionManager(backend, config)

        dep_app = FastAPI()
        dep_app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

        @dep_app.get("/protected")
        async def protected(session=Depends(require_session)):
            return {"user_id": session.user.user_id if session.user else None}

        dep_client = TestClient(dep_app, raise_server_exceptions=False)

        # No session token → 401
        r = dep_client.get("/protected")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_require_session_with_valid_session(self) -> None:
        """require_session passes when a valid session is present."""
        config = SessionConfig(secret_key="a" * 32)
        backend = MemoryBackend()
        manager = SessionManager(backend, config)

        dep_app = FastAPI()
        dep_app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

        @dep_app.get("/me")
        async def me(session=Depends(require_session)):
            return {"user_id": session.user.user_id if session.user else None}

        dep_client = TestClient(dep_app)

        user = SessionUser(user_id="u1", username="alice")
        _session, token = await manager.create_session(user=user)

        r = dep_client.get("/me", headers={"X-Session-Token": token})
        assert r.status_code == 200
        assert r.json()["user_id"] == "u1"
