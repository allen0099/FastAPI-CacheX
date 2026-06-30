"""JWT token serializer integration tests.

These tests are skipped if PyJWT is not installed.
"""

from __future__ import annotations

import asyncio

import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.exceptions import SessionTokenError
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.models import SessionUser

jwt = pytest.importorskip("jwt")


@pytest.mark.asyncio
async def test_jwt_create_and_get_session() -> None:
    backend = MemoryBackend()
    config = SessionConfig(
        secret_key="a" * 32,
        token_format="jwt",
        jwt_algorithm="HS256",
        jwt_issuer="test-iss",
        jwt_audience="test-aud",
        session_ttl=3600,
    )
    manager = SessionManager(backend, config)

    user = SessionUser(user_id="u1", username="alice")
    created, token = await manager.create_session(user=user)

    retrieved, _ = await manager.get_session(token)
    assert retrieved.session_id == created.session_id
    assert retrieved.user is not None
    assert retrieved.user.user_id == "u1"


@pytest.mark.asyncio
async def test_jwt_invalid_signature_rejected() -> None:
    backend = MemoryBackend()
    config = SessionConfig(secret_key="a" * 32, token_format="jwt")
    manager = SessionManager(backend, config)

    _session, token = await manager.create_session(user=SessionUser(user_id="u1"))

    # Tamper token by flipping a character near the end
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]

    with pytest.raises(SessionTokenError):
        await manager.get_session(tampered)


@pytest.mark.asyncio
async def test_jwt_wrong_audience_rejected() -> None:
    backend = MemoryBackend()
    config1 = SessionConfig(
        secret_key="a" * 32,
        token_format="jwt",
        jwt_audience="aud1",
    )
    manager1 = SessionManager(backend, config1)
    _session, token = await manager1.create_session(user=SessionUser(user_id="u1"))

    # A different manager expecting different audience should reject
    config2 = SessionConfig(
        secret_key="a" * 32,
        token_format="jwt",
        jwt_audience="aud2",
    )
    manager2 = SessionManager(backend, config2)

    with pytest.raises(SessionTokenError):
        await manager2.get_session(token)


@pytest.mark.asyncio
async def test_jwt_expiration_enforced() -> None:
    backend = MemoryBackend()
    config = SessionConfig(secret_key="a" * 32, token_format="jwt", session_ttl=1)
    manager = SessionManager(backend, config)

    _session, token = await manager.create_session(user=SessionUser(user_id="u1"))
    await asyncio.sleep(1.2)

    # JWT should be expired before reaching session checks
    with pytest.raises(SessionTokenError):
        await manager.get_session(token)


@pytest.mark.asyncio
async def test_jwt_sliding_renewal_returns_new_token_with_updated_exp() -> None:
    """Sliding renewal must return a new JWT whose exp reflects the extended expiry."""
    from datetime import datetime, timedelta, timezone

    backend = MemoryBackend()
    config = SessionConfig(
        secret_key="a" * 32,
        token_format="jwt",
        jwt_algorithm="HS256",
        session_ttl=3600,
        sliding_expiration=True,
        sliding_threshold=0.5,
    )
    manager = SessionManager(backend, config)

    created, original_token = await manager.create_session(
        user=SessionUser(user_id="u1")
    )

    # Shorten expires_at so time_remaining < 50% of 3600 s → triggers renewal
    created.expires_at = datetime.now(timezone.utc) + timedelta(seconds=1000)
    await manager._save_session(created)

    _, renewed_token = await manager.get_session(original_token)

    assert renewed_token is not None
    assert renewed_token != original_token

    # The renewed token must be immediately usable (exp updated, not stale)
    retrieved, _ = await manager.get_session(renewed_token)
    assert retrieved.session_id == created.session_id
