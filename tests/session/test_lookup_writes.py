"""A session lookup writes to the backend only when something needs storing (#115).

Before 0.3.8 every ``get_session()`` re-saved the session to record
``last_accessed``, so each authenticated request cost a write, and one that
also modified the session cost two.
"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.models import Session
from fastapi_cachex.session.models import SessionUser
from fastapi_cachex.types import CacheEntry


class SpyBackend(MemoryBackend):
    """A memory backend that records every key passed to ``set`` or ``set_if_equals``."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    async def set(
        self, key: str, value: CacheEntry, ttl: int | timedelta | None = None
    ) -> None:
        self.writes.append(key)
        await super().set(key, value, ttl=ttl)

    async def set_if_equals(
        self,
        key: str,
        expected: CacheEntry,
        value: CacheEntry,
        ttl: int | timedelta | None = None,
    ) -> bool:
        self.writes.append(key)
        return await super().set_if_equals(key, expected, value, ttl=ttl)


def _manager(**overrides: object) -> tuple[SessionManager, SpyBackend]:
    backend = SpyBackend()
    config = SessionConfig(
        secret_key="a" * 32, cookie_name="session", cookie_https_only=False, **overrides
    )
    return SessionManager(backend, config), backend


async def _stored(manager: SessionManager, session_id: str) -> Session:
    stored = await manager._load_session(session_id)
    assert stored is not None
    return stored


@pytest.mark.parametrize("sliding_expiration", [True, False])
async def test_plain_lookup_does_not_write(sliding_expiration: bool) -> None:
    """A lookup that renews nothing leaves the backend untouched."""
    manager, backend = _manager(sliding_expiration=sliding_expiration)
    session, token = await manager.create_session(SessionUser(user_id="u1"))
    backend.writes.clear()

    loaded, renewed = await manager.get_session(token)

    assert renewed is None
    assert backend.writes == []
    assert loaded.session_id == session.session_id


async def test_sliding_renewal_writes_once_and_extends_the_stored_expiry() -> None:
    """A renewal must reach the backend, or its TTL would not move."""
    manager, backend = _manager(session_ttl=3600, sliding_threshold=0.5)
    session, token = await manager.create_session(SessionUser(user_id="u1"))
    session.expires_at = datetime.now(timezone.utc) + timedelta(seconds=100)
    await manager.update_session(session)
    backend.writes.clear()

    loaded, renewed = await manager.get_session(token)

    assert renewed is not None
    assert len(backend.writes) == 1
    stored = await _stored(manager, session.session_id)
    assert stored.expires_at == loaded.expires_at
    assert stored.expires_at > datetime.now(timezone.utc) + timedelta(seconds=3000)  # type: ignore[operator]


async def test_touch_stores_last_accessed() -> None:
    """``touch=True`` keeps the stored ``last_accessed`` exact."""
    manager, backend = _manager()
    session, token = await manager.create_session(SessionUser(user_id="u1"))
    backend.writes.clear()

    untouched, _ = await manager.get_session(token)
    assert backend.writes == []
    assert (await _stored(manager, session.session_id)).last_accessed < (
        untouched.last_accessed
    )

    touched, _ = await manager.get_session(token, touch=True)
    assert len(backend.writes) == 1
    stored = await _stored(manager, session.session_id)
    assert stored.last_accessed == touched.last_accessed


async def test_session_entries_use_a_constant_fingerprint() -> None:
    """Nothing compares session fingerprints, so the payload is not hashed."""
    manager, backend = _manager()
    session, _ = await manager.create_session(SessionUser(user_id="u1"))

    entry = await backend.get(manager._get_backend_key(session.session_id))

    assert entry is not None
    assert entry.fingerprint == "session"


def test_middleware_reads_without_writing_and_writes_changes_once() -> None:
    """Reading ``request.session`` costs no write; changing it costs exactly one."""
    manager, backend = _manager()
    app = FastAPI()
    app.add_middleware(FastAPICacheXSessionMiddleware, session_manager=manager)

    @app.get("/read")
    async def read(request: Request) -> dict[str, object]:
        return {"count": request.session.get("count", 0)}

    @app.get("/bump")
    async def bump(request: Request) -> dict[str, object]:
        request.session["count"] = request.session.get("count", 0) + 1
        return {"count": request.session["count"]}

    client = TestClient(app)
    assert client.get("/bump").json() == {"count": 1}  # creates the session
    backend.writes.clear()

    assert client.get("/read").json() == {"count": 1}
    assert backend.writes == []

    assert client.get("/bump").json() == {"count": 2}
    assert len(backend.writes) == 1
    assert client.get("/read").json() == {"count": 2}
