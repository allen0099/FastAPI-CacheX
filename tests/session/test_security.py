"""Tests for security utilities."""

import pytest

from fastapi_cachex.session.models import Session
from fastapi_cachex.session.security import SecurityManager


def test_security_manager_initialization() -> None:
    """Test security manager initialization."""
    manager = SecurityManager("a" * 32)
    assert manager.secret_key is not None


def test_security_manager_short_key() -> None:
    """Test security manager with short key."""
    with pytest.raises(ValueError, match="at least 32 characters"):
        SecurityManager("short")


def test_sign_session_id() -> None:
    """Test session ID signing."""
    manager = SecurityManager("a" * 32)
    signature = manager.sign_session_id("test-session-id")

    assert signature is not None
    assert len(signature) == 64  # SHA256 hex digest


def test_verify_signature() -> None:
    """Test signature verification."""
    manager = SecurityManager("a" * 32)
    session_id = "test-session-id"
    signature = manager.sign_session_id(session_id)

    # Valid signature
    assert manager.verify_signature(session_id, signature)

    # Invalid signature
    assert not manager.verify_signature(session_id, "invalid")

    # Different session ID
    assert not manager.verify_signature("different-id", signature)


def test_generate_csrf_token() -> None:
    """Test CSRF token generation."""
    manager = SecurityManager("a" * 32)
    token1 = manager.generate_csrf_token()
    token2 = manager.generate_csrf_token()

    assert token1 != token2
    assert len(token1) > 20


def test_verify_csrf_token() -> None:
    """Test CSRF token verification."""
    manager = SecurityManager("a" * 32)
    token = manager.generate_csrf_token()

    # Valid token
    assert manager.verify_csrf_token(token, token)

    # Invalid token
    assert not manager.verify_csrf_token(token, "different")


def test_check_ip_match() -> None:
    """Test IP address matching."""
    manager = SecurityManager("a" * 32)

    # Session without IP binding
    session = Session()
    assert manager.check_ip_match(session, "192.168.1.1")
    assert manager.check_ip_match(session, None)

    # Session with IP binding
    session.ip_address = "192.168.1.1"
    assert manager.check_ip_match(session, "192.168.1.1")
    assert not manager.check_ip_match(session, "192.168.1.2")
    assert not manager.check_ip_match(session, None)


def test_check_user_agent_match() -> None:
    """Test User-Agent matching."""
    manager = SecurityManager("a" * 32)

    # Session without UA binding
    session = Session()
    assert manager.check_user_agent_match(session, "Mozilla/5.0")
    assert manager.check_user_agent_match(session, None)

    # Session with UA binding
    session.user_agent = "Mozilla/5.0"
    assert manager.check_user_agent_match(session, "Mozilla/5.0")
    assert not manager.check_user_agent_match(session, "Chrome/91.0")
    assert not manager.check_user_agent_match(session, None)


def test_hash_data() -> None:
    """Test data hashing."""
    manager = SecurityManager("a" * 32)
    hash1 = manager.hash_data("test data")
    hash2 = manager.hash_data("test data")
    hash3 = manager.hash_data("different data")

    assert hash1 == hash2
    assert hash1 != hash3
    assert len(hash1) == 64  # SHA256 hex digest
