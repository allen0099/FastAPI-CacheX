"""Tests for session models."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

from fastapi_cachex.session.models import Session
from fastapi_cachex.session.models import SessionStatus
from fastapi_cachex.session.models import SessionToken
from fastapi_cachex.session.models import SessionUser


def test_session_user_creation() -> None:
    """Test creating a session user."""
    user = SessionUser(user_id="123", username="testuser", email="test@example.com")
    assert user.user_id == "123"
    assert user.username == "testuser"
    assert user.email == "test@example.com"
    assert user.roles == []
    assert user.permissions == []


def test_session_user_with_roles() -> None:
    """Test session user with roles and permissions."""
    user = SessionUser(
        user_id="123",
        username="testuser",
        roles=["admin", "user"],
        permissions=["read", "write"],
    )
    assert "admin" in user.roles
    assert "write" in user.permissions


def test_session_creation() -> None:
    """Test creating a session."""
    session = Session()
    assert session.session_id is not None
    assert session.status == SessionStatus.ACTIVE
    assert session.user is None
    assert isinstance(session.created_at, datetime)
    assert isinstance(session.last_accessed, datetime)


def test_session_with_user() -> None:
    """Test session with user data."""
    user = SessionUser(user_id="123", username="testuser")
    session = Session(user=user)
    assert session.user is not None
    assert session.user.user_id == "123"


def test_session_is_valid() -> None:
    """Test session validity check."""
    session = Session()
    assert session.is_valid()

    # Expired session
    session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    assert not session.is_valid()

    # Invalidated session
    session.expires_at = None
    session.status = SessionStatus.INVALIDATED
    assert not session.is_valid()


def test_session_is_expired() -> None:
    """Test session expiry check."""
    session = Session()
    assert not session.is_expired()

    session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    assert session.is_expired()

    session.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    assert not session.is_expired()


def test_session_renew() -> None:
    """Test session renewal."""
    import time

    session = Session()
    session.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    old_expiry = session.expires_at

    # Sleep briefly to ensure time difference
    time.sleep(0.01)

    session.renew(3600)  # Renew for 1 hour

    assert session.expires_at >= old_expiry


def test_session_regenerate_id() -> None:
    """Test session ID regeneration."""
    session = Session()
    old_id = session.session_id

    new_id = session.regenerate_id()

    assert new_id != old_id
    assert session.session_id == new_id


def test_session_flash_messages() -> None:
    """Test flash message functionality."""
    session = Session()

    session.add_flash_message("Hello", "info")
    session.add_flash_message("Error occurred", "error")

    messages = session.get_flash_messages(clear=False)
    assert len(messages) == 2
    assert messages[0]["message"] == "Hello"
    assert messages[1]["category"] == "error"

    # Still has messages
    assert len(session.flash_messages) == 2

    # Clear messages
    messages = session.get_flash_messages(clear=True)
    assert len(messages) == 2
    assert len(session.flash_messages) == 0


def test_session_token_to_string() -> None:
    """Test session token string conversion."""
    token = SessionToken(session_id="test123", signature="abc123")
    token_str = token.to_string()

    assert "test123" in token_str
    assert "abc123" in token_str
    assert token_str.count(".") == 2


def test_session_token_from_string() -> None:
    """Test session token parsing."""
    token = SessionToken(session_id="test123", signature="abc123")
    token_str = token.to_string()

    parsed = SessionToken.from_string(token_str)

    assert parsed.session_id == "test123"
    assert parsed.signature == "abc123"


def test_session_token_invalid_format() -> None:
    """Test session token with invalid format."""
    with pytest.raises(ValueError, match="Invalid token format"):
        SessionToken.from_string("invalid")

    with pytest.raises(ValueError, match="Invalid token format"):
        SessionToken.from_string("only.two")


def test_session_token_invalid_timestamp() -> None:
    """Test session token with invalid timestamp."""
    with pytest.raises(ValueError, match="Invalid timestamp"):
        SessionToken.from_string("test123.abc123.invalid")
