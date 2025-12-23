from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi import Request

from fastapi_cachex.session.dependencies import get_optional_session
from fastapi_cachex.session.dependencies import get_session


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
