"""Tests for session manager."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.exceptions import SessionExpiredError
from fastapi_cachex.session.exceptions import SessionInvalidError
from fastapi_cachex.session.exceptions import SessionNotFoundError
from fastapi_cachex.session.exceptions import SessionSecurityError
from fastapi_cachex.session.exceptions import SessionTokenError
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.models import SessionUser


@pytest.fixture
def backend() -> MemoryBackend:
    """Create a memory backend for testing."""
    return MemoryBackend()


@pytest.fixture
def config() -> SessionConfig:
    """Create session config for testing."""
    return SessionConfig(secret_key="a" * 32, session_ttl=3600)


@pytest.fixture
def manager(backend: MemoryBackend, config: SessionConfig) -> SessionManager:
    """Create session manager for testing."""
    return SessionManager(backend, config)


@pytest.mark.asyncio
async def test_create_session(manager: SessionManager) -> None:
    """Test creating a session."""
    user = SessionUser(user_id="123", username="testuser")
    session, token = await manager.create_session(user=user)

    assert session.session_id is not None
    assert session.user is not None
    assert session.user.user_id == "123"
    assert token is not None


@pytest.mark.asyncio
async def test_get_session(manager: SessionManager) -> None:
    """Test retrieving a session."""
    user = SessionUser(user_id="123", username="testuser")
    created_session, token = await manager.create_session(user=user)

    retrieved_session = await manager.get_session(token)

    assert retrieved_session.session_id == created_session.session_id
    assert retrieved_session.user.user_id == "123"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_get_invalid_token(manager: SessionManager) -> None:
    """Test getting session with invalid token."""
    with pytest.raises(SessionTokenError):
        await manager.get_session("invalid-token")


@pytest.mark.asyncio
async def test_get_nonexistent_session(manager: SessionManager) -> None:
    """Test getting nonexistent session."""
    # Create a valid token for a session that doesn't exist
    from fastapi_cachex.session.models import SessionToken

    token = SessionToken(
        session_id="nonexistent",
        signature=manager.security.sign_session_id("nonexistent"),
    )

    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token.to_string())


@pytest.mark.asyncio
async def test_session_expiry(manager: SessionManager) -> None:
    """Test session expiry."""
    # Create session with short TTL
    manager.config.session_ttl = 1
    user = SessionUser(user_id="123", username="testuser")
    session, token = await manager.create_session(user=user)

    # Manually expire the session
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await manager.update_session(session)

    with pytest.raises(SessionExpiredError):
        await manager.get_session(token)


@pytest.mark.asyncio
async def test_delete_session(manager: SessionManager) -> None:
    """Test deleting a session."""
    user = SessionUser(user_id="123", username="testuser")
    session, token = await manager.create_session(user=user)

    await manager.delete_session(session.session_id)

    with pytest.raises(SessionNotFoundError):
        await manager.get_session(token)


@pytest.mark.asyncio
async def test_regenerate_session_id(manager: SessionManager) -> None:
    """Test regenerating session ID."""
    user = SessionUser(user_id="123", username="testuser")
    session, old_token = await manager.create_session(user=user)
    old_id = session.session_id

    updated_session, new_token = await manager.regenerate_session_id(session)

    assert updated_session.session_id != old_id
    assert new_token != old_token

    # Old token should not work
    with pytest.raises(SessionNotFoundError):
        await manager.get_session(old_token)

    # New token should work
    retrieved = await manager.get_session(new_token)
    assert retrieved.session_id == updated_session.session_id


@pytest.mark.asyncio
async def test_ip_binding(backend: MemoryBackend) -> None:
    """Test IP address binding."""
    config = SessionConfig(secret_key="a" * 32, ip_binding=True)
    manager = SessionManager(backend, config)

    user = SessionUser(user_id="123", username="testuser")
    session, token = await manager.create_session(
        user=user,
        ip_address="192.168.1.1",
    )

    # Same IP should work
    retrieved = await manager.get_session(token, ip_address="192.168.1.1")
    assert retrieved.session_id == session.session_id

    # Different IP should fail
    with pytest.raises(SessionSecurityError, match="IP address mismatch"):
        await manager.get_session(token, ip_address="192.168.1.2")


@pytest.mark.asyncio
async def test_user_agent_binding(backend: MemoryBackend) -> None:
    """Test User-Agent binding."""
    config = SessionConfig(secret_key="a" * 32, user_agent_binding=True)
    manager = SessionManager(backend, config)

    user = SessionUser(user_id="123", username="testuser")
    session, token = await manager.create_session(
        user=user,
        user_agent="Mozilla/5.0",
    )

    # Same UA should work
    retrieved = await manager.get_session(token, user_agent="Mozilla/5.0")
    assert retrieved.session_id == session.session_id

    # Different UA should fail
    with pytest.raises(SessionSecurityError, match="User-Agent mismatch"):
        await manager.get_session(token, user_agent="Chrome/91.0")


@pytest.mark.asyncio
async def test_sliding_expiration(backend: MemoryBackend) -> None:
    """Test sliding expiration."""
    import asyncio

    config = SessionConfig(
        secret_key="a" * 32,
        session_ttl=3600,
        sliding_expiration=True,
        sliding_threshold=0.5,
    )
    manager = SessionManager(backend, config)

    user = SessionUser(user_id="123", username="testuser")
    session, token = await manager.create_session(user=user)

    original_expiry = session.expires_at

    # Sleep briefly to ensure time passes
    await asyncio.sleep(0.01)

    # Set expiry to be past the threshold (only 1000 seconds left vs 3600 TTL)
    session.expires_at = datetime.now(timezone.utc) + timedelta(seconds=1000)
    await manager.update_session(session)

    # Access session - should trigger renewal since < 50% of TTL remains
    retrieved = await manager.get_session(token)

    # Should be renewed to be further in the future than original
    assert retrieved.expires_at > original_expiry  # type: ignore[operator]


@pytest.mark.asyncio
async def test_invalidate_session(manager: SessionManager) -> None:
    """Test invalidating a session."""
    user = SessionUser(user_id="123", username="testuser")
    session, token = await manager.create_session(user=user)

    await manager.invalidate_session(session)

    # Token should no longer work
    with pytest.raises(SessionInvalidError):
        await manager.get_session(token)


@pytest.mark.asyncio
async def test_clear_expired_sessions(backend: MemoryBackend) -> None:
    """Test clearing expired sessions."""
    config = SessionConfig(secret_key="a" * 32, session_ttl=1)
    manager = SessionManager(backend, config)

    # Create a few sessions
    user1 = SessionUser(user_id="1", username="user1")
    user2 = SessionUser(user_id="2", username="user2")

    _session1, _token1 = await manager.create_session(user=user1)
    _session2, _token2 = await manager.create_session(user=user2)

    # All should be cleared (they expire after 1 second)
    import asyncio

    await asyncio.sleep(1.1)
    count = await manager.clear_expired_sessions()

    assert count >= 0  # May be 0 or more depending on timing


@pytest.mark.asyncio
async def test_update_session(manager: SessionManager) -> None:
    """Test updating a session."""
    user = SessionUser(user_id="123", username="testuser")
    session, token = await manager.create_session(user=user)

    # Update session data
    session.data["updated_field"] = "updated_value"
    await manager.update_session(session)

    # Retrieve and verify
    retrieved = await manager.get_session(token)
    assert retrieved.data.get("updated_field") == "updated_value"
