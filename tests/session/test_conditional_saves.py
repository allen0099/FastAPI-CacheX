"""Ordinary session saves are conditional (#128).

A request that loaded a session must not bring it back after another request
deleted, invalidated or rotated it, whether by saving it or by rotating its
ID. Deleting, invalidating and expiring stay unconditional, so a security
action always wins.
"""

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import login
from fastapi_cachex.session import rotate_session_id
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.exceptions import SessionInvalidError
from fastapi_cachex.session.exceptions import SessionNotFoundError
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.models import Session
from fastapi_cachex.session.models import SessionStatus
from fastapi_cachex.session.models import SessionUser
from fastapi_cachex.session.proxy import SessionManagerProxy
from fastapi_cachex.types import CacheEntry
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import flush_memcached
from tests.live_servers import requires_memcached
from tests.live_servers import requires_redis
from tests.live_servers import requires_redis_package

from .test_login import _auth
from .test_login import _token_sent

if TYPE_CHECKING:
    from fastapi_cachex.backends.base import BaseCacheBackend


@pytest_asyncio.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param(
            "redis", id="redis", marks=[requires_redis, requires_redis_package]
        ),
        pytest.param("memcached", id="memcached", marks=[requires_memcached]),
    ]
)
async def any_backend(request: Any) -> AsyncGenerator["BaseCacheBackend", Any]:
    """A memory backend, and live Redis / Memcached ones when available."""
    if request.param == "memory":
        yield MemoryBackend()
        return
    if request.param == "redis":
        from fastapi_cachex.backends import AsyncRedisCacheBackend

        redis_backend = AsyncRedisCacheBackend(
            host=REDIS_HOST, port=REDIS_PORT, key_prefix="test_conditional_saves:"
        )
        await redis_backend.clear()
        yield redis_backend
        await redis_backend.clear()
        return
    from fastapi_cachex.backends import MemcachedBackend

    memcached_backend = MemcachedBackend(servers=[MEMCACHED_SERVER])
    await flush_memcached(memcached_backend)
    yield memcached_backend
    await flush_memcached(memcached_backend)


@pytest.fixture
def any_manager(
    any_backend: "BaseCacheBackend", config: SessionConfig
) -> SessionManager:
    return SessionManager(any_backend, config)


async def _two_copies(manager: SessionManager) -> tuple[Session, Session, str]:
    """A stored session read by two requests, and its token."""
    _session, token = await manager.create_session(SessionUser(user_id="alice"))
    first, _ = await manager.get_session(token)
    second, _ = await manager.get_session(token)
    return first, second, token


# --- SessionManager -----------------------------------------------------------


async def test_a_stale_save_cannot_restore_a_deleted_session(
    any_manager: SessionManager,
) -> None:
    stale, _other, token = await _two_copies(any_manager)
    await any_manager.delete_session(stale.session_id)

    stale.data["cart"] = ["book"]
    assert await any_manager.update_session(stale) is False

    assert await any_manager._load_session(stale.session_id) is None
    with pytest.raises(SessionNotFoundError):
        await any_manager.get_session(token)


async def test_a_stale_save_cannot_reactivate_an_invalidated_session(
    any_manager: SessionManager,
) -> None:
    stale, other, token = await _two_copies(any_manager)
    await any_manager.invalidate_session(other)

    stale.data["cart"] = ["book"]
    assert await any_manager.update_session(stale) is False

    stored = await any_manager._load_session(stale.session_id)
    assert stored is not None
    assert stored.status == SessionStatus.INVALIDATED
    assert "cart" not in stored.data
    with pytest.raises(SessionInvalidError):
        await any_manager.get_session(token)


async def test_a_stale_save_cannot_restore_the_id_before_a_rotation(
    any_manager: SessionManager,
) -> None:
    """After a rotation, the old token must stay dead."""
    stale, other, old_token = await _two_copies(any_manager)
    old_id = stale.session_id
    _rotated, new_token = await any_manager.regenerate_session_id(other)

    stale.data["cart"] = ["book"]
    assert await any_manager.update_session(stale) is False

    assert await any_manager._load_session(old_id) is None
    with pytest.raises(SessionNotFoundError):
        await any_manager.get_session(old_token)
    assert (await any_manager.get_session(new_token))[0].data == {}


async def test_the_first_of_two_concurrent_saves_wins(
    any_manager: SessionManager,
) -> None:
    """Two tabs change one session: the second save is dropped, not merged (#376)."""
    first, second, _token = await _two_copies(any_manager)

    first.data["cart"] = ["book"]
    assert await any_manager.update_session(first) is True
    second.data["cart"] = ["pen"]
    assert await any_manager.update_session(second) is False

    stored = await any_manager._load_session(first.session_id)
    assert stored is not None
    assert stored.data == {"cart": ["book"]}


async def test_saves_compare_against_the_objects_own_last_write(
    any_manager: SessionManager,
) -> None:
    session, _other, _token = await _two_copies(any_manager)

    for count in range(3):
        session.data["count"] = count
        assert await any_manager.update_session(session) is True

    stored = await any_manager._load_session(session.session_id)
    assert stored is not None
    assert stored.data == {"count": 2}


async def test_unconditional_writes_win_over_a_newer_save(
    manager: SessionManager,
) -> None:
    """Invalidating a stale copy still invalidates: security actions always win."""
    stale, newer, token = await _two_copies(manager)
    newer.data["cart"] = ["book"]
    assert await manager.update_session(newer) is True

    await manager.invalidate_session(stale)

    with pytest.raises(SessionInvalidError):
        await manager.get_session(token)


async def test_a_session_never_read_is_saved_only_if_absent(
    manager: SessionManager,
) -> None:
    """A hand-built ``Session`` holds no stored entry, so it expects no record."""
    fresh = Session(data={"a": 1})
    assert await manager.update_session(fresh) is True
    assert (await manager._load_session(fresh.session_id)) is not None

    existing, _token = await manager.create_session(SessionUser(user_id="alice"))
    impostor = Session(session_id=existing.session_id, data={"planted": True})
    assert await manager.update_session(impostor) is False
    stored = await manager._load_session(existing.session_id)
    assert stored is not None
    assert stored.data == {}


async def test_a_dropped_save_is_logged(
    manager: SessionManager, caplog: pytest.LogCaptureFixture
) -> None:
    stale, _other, _token = await _two_copies(manager)
    await manager.delete_session(stale.session_id)

    with caplog.at_level(logging.INFO, logger="fastapi_cachex.session.manager"):
        assert await manager.update_session(stale) is False

    assert any(
        record.levelno == logging.INFO and "Session save dropped" in record.message
        for record in caplog.records
    )


# --- Sliding renewal ----------------------------------------------------------


@pytest.fixture
def renewing_config() -> SessionConfig:
    """A config under which every lookup renews the session."""
    return SessionConfig(
        secret_key="a" * 32,
        cookie_name="session",
        cookie_https_only=False,
        sliding_threshold=1.0,
    )


@pytest.fixture
def renewing(backend: MemoryBackend, renewing_config: SessionConfig) -> SessionManager:
    """A manager whose every lookup renews the session."""
    return SessionManager(backend, renewing_config)


async def test_a_save_after_a_renewal_compares_against_the_renewal(
    renewing: SessionManager,
) -> None:
    _session, token = await renewing.create_session(SessionUser(user_id="alice"))
    session, renewed = await renewing.get_session(token)
    assert renewed is not None

    session.data["cart"] = ["book"]
    assert await renewing.update_session(session) is True


def _before_first_conditional_write(
    backend: MemoryBackend, monkeypatch: pytest.MonkeyPatch, action: Any
) -> None:
    """Run ``action`` once, just before the first ``set_if_equals`` compares."""
    original = backend.set_if_equals
    pending = [action]

    async def racing(
        key: str, expected: CacheEntry, value: CacheEntry, ttl: int | None = None
    ) -> bool:
        if pending:
            await pending.pop()()
        return await original(key, expected, value, ttl=ttl)

    monkeypatch.setattr(backend, "set_if_equals", racing)


async def test_a_renewal_that_loses_to_an_invalidation_is_refused(
    renewing: SessionManager,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, token = await renewing.create_session(SessionUser(user_id="alice"))

    async def invalidate_elsewhere() -> None:
        other = await renewing._load_session(session.session_id)
        assert other is not None
        await renewing.invalidate_session(other)

    _before_first_conditional_write(backend, monkeypatch, invalidate_elsewhere)

    with pytest.raises(SessionInvalidError):
        await renewing.get_session(token)
    stored = await renewing._load_session(session.session_id)
    assert stored is not None
    assert stored.status == SessionStatus.INVALIDATED


async def test_a_renewal_that_loses_to_a_save_reads_the_session_again(
    renewing: SessionManager,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, token = await renewing.create_session(SessionUser(user_id="alice"))

    async def save_elsewhere() -> None:
        other = await renewing._load_session(session.session_id)
        assert other is not None
        other.data["cart"] = ["book"]
        assert await renewing.update_session(other) is True

    _before_first_conditional_write(backend, monkeypatch, save_elsewhere)

    loaded, _renewed = await renewing.get_session(token)

    assert loaded.data == {"cart": ["book"]}
    stored = await renewing._load_session(session.session_id)
    assert stored is not None
    assert stored.data == {"cart": ["book"]}


async def test_a_renewal_that_loses_twice_is_dropped(
    renewing: SessionManager,
    backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session was valid when read again, so the request keeps it."""
    session, token = await renewing.create_session(SessionUser(user_id="alice"))
    calls = 0

    async def always_lose(*_args: object, **_kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(backend, "set_if_equals", always_lose)

    loaded, renewed = await renewing.get_session(token)

    assert calls == 2
    assert loaded.session_id == session.session_id
    assert renewed is None


# --- FastAPICacheXSessionMiddleware ---------------------------------------------


def _app(manager: SessionManager, config: SessionConfig, action: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.post("/cart")
    async def add_to_cart(request: Request) -> dict[str, bool]:
        request.session["cart"] = ["book"]
        # Another request logs the session out while this one is running.
        session_id = request.session.backend.session_id  # type: ignore[attr-defined]
        if action == "delete":
            await manager.delete_session(session_id)
        elif action == "save":
            other = await manager._load_session(session_id)
            assert other is not None
            other.data["cart"] = ["pen"]
            assert await manager.update_session(other) is True
        else:
            other = await manager._load_session(session_id)
            assert other is not None
            await manager.invalidate_session(other)
        return {"ok": True}

    return app


@pytest.mark.parametrize("action", ["delete", "invalidate"])
@pytest.mark.parametrize("transport", ["cookie", "header"])
async def test_the_middleware_drops_a_stale_save_and_sends_no_token(
    manager: SessionManager, config: SessionConfig, action: str, transport: str
) -> None:
    session, token = await manager.create_session(SessionUser(user_id="alice"))
    client = TestClient(_app(manager, config, action))
    if transport == "cookie":
        client.cookies.set(config.cookie_name, token)
        response = client.post("/cart")
    else:
        response = client.post("/cart", headers=_auth(transport, config, token))

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert config.header_name.lower() not in response.headers
    stored = await manager._load_session(session.session_id)
    if action == "delete":
        assert stored is None
    else:
        assert stored is not None
        assert stored.status == SessionStatus.INVALIDATED
        assert "cart" not in stored.data


@pytest.mark.parametrize("transport", ["cookie", "header"])
async def test_a_dropped_save_still_sends_a_stored_renewal(
    backend: MemoryBackend, transport: str
) -> None:
    """The save lost to another save, but the renewal was stored: send its token.

    Without it, a JWT client would keep a token whose ``exp`` comes before the
    renewed record's expiry. The session starts with a short TTL, so the
    renewed JWT differs from the one the client sent.
    """
    settings: dict[str, Any] = {
        "secret_key": "a" * 32,
        "cookie_name": "session",
        "cookie_https_only": False,
        "token_format": "jwt",
    }
    short = SessionManager(backend, SessionConfig(**settings, session_ttl=60))
    renewing_config = SessionConfig(**settings, sliding_threshold=1.0)
    renewing = SessionManager(backend, renewing_config)
    session, token = await short.create_session(SessionUser(user_id="alice"))
    client = TestClient(_app(renewing, renewing_config, "save"))
    if transport == "cookie":
        client.cookies.set(renewing_config.cookie_name, token)
        response = client.post("/cart")
    else:
        response = client.post(
            "/cart", headers=_auth(transport, renewing_config, token)
        )

    assert response.status_code == 200
    sent = _token_sent(response, transport, renewing_config)
    assert sent != token
    loaded, _ = await renewing.get_session(sent)
    assert loaded.session_id == session.session_id
    assert loaded.data == {"cart": ["pen"]}


def test_sessions_compare_equal_whatever_entry_they_last_saw() -> None:
    """``_stored`` is bookkeeping, not part of the session's value."""
    session = Session(data={"a": 1})
    loaded = Session.model_validate_json(session.model_dump_json())
    loaded._stored = CacheEntry(fingerprint="session", content=b"x")

    assert loaded == session
    assert loaded != Session(data={"a": 2})
    assert session.__eq__("not a session") is NotImplemented


async def test_a_stored_renewal_is_not_sent_for_an_invalidated_session(
    renewing: SessionManager, renewing_config: SessionConfig
) -> None:
    _session, token = await renewing.create_session(SessionUser(user_id="alice"))
    client = TestClient(_app(renewing, renewing_config, "invalidate"))
    client.cookies.set(renewing_config.cookie_name, token)

    response = client.post("/cart")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


# --- ID rotation ----------------------------------------------------------------


async def test_a_stale_copy_of_a_deleted_session_cannot_be_rotated(
    any_manager: SessionManager,
) -> None:
    stale, _other, token = await _two_copies(any_manager)
    old_id = stale.session_id
    await any_manager.delete_session(old_id)

    with pytest.raises(SessionNotFoundError):
        await any_manager.regenerate_session_id(stale)

    assert stale.session_id == old_id
    assert await any_manager._load_session(old_id) is None
    with pytest.raises(SessionNotFoundError):
        await any_manager.get_session(token)


async def test_a_stale_copy_of_an_invalidated_session_cannot_be_rotated(
    any_manager: SessionManager,
) -> None:
    stale, other, token = await _two_copies(any_manager)
    old_id = stale.session_id
    await any_manager.invalidate_session(other)

    with pytest.raises(SessionInvalidError):
        await any_manager.regenerate_session_id(stale)

    assert stale.session_id == old_id
    with pytest.raises(SessionNotFoundError):
        await any_manager.get_session(token)


async def test_a_stale_copy_of_an_expired_session_cannot_be_rotated(
    manager: SessionManager,
) -> None:
    """``is_valid()`` covers expiry too, not only the status."""
    stale, other, _token = await _two_copies(manager)
    other.expires_at = other.created_at
    assert await manager.update_session(other) is True

    with pytest.raises(SessionInvalidError):
        await manager.regenerate_session_id(stale)


async def test_only_one_of_two_concurrent_rotations_succeeds(
    any_manager: SessionManager,
) -> None:
    first, second, _token = await _two_copies(any_manager)

    _rotated, new_token = await any_manager.regenerate_session_id(first)
    with pytest.raises(SessionNotFoundError):
        await any_manager.regenerate_session_id(second)

    assert (await any_manager.get_session(new_token))[0].session_id == (
        first.session_id
    )


async def test_a_rotation_after_another_save_goes_ahead(
    any_manager: SessionManager,
) -> None:
    """The session is still valid; only its data changed."""
    stale, other, _token = await _two_copies(any_manager)
    other.data["cart"] = ["book"]
    assert await any_manager.update_session(other) is True

    rotated, new_token = await any_manager.regenerate_session_id(stale)

    assert (await any_manager.get_session(new_token))[0].session_id == (
        rotated.session_id
    )


async def test_a_session_never_read_is_rotated_without_the_check(
    manager: SessionManager,
) -> None:
    fresh = Session(data={"a": 1})

    _rotated, new_token = await manager.regenerate_session_id(fresh)

    assert (await manager.get_session(new_token))[0].data == {"a": 1}


def _rotating_app(manager: SessionManager, config: SessionConfig) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    async def log_out_elsewhere(request: Request) -> None:
        session_id = request.session.backend.session_id  # type: ignore[attr-defined]
        other = await manager._load_session(session_id)
        assert other is not None
        await manager.invalidate_session(other)

    @app.post("/sudo")
    async def sudo(request: Request) -> dict[str, bool]:
        request.session["seen"] = True
        await log_out_elsewhere(request)
        await rotate_session_id(request)
        request.session["elevated"] = True
        return {"ok": True}

    @app.post("/sudo-quiet")
    async def sudo_quiet(request: Request) -> dict[str, bool]:
        await log_out_elsewhere(request)
        await rotate_session_id(request)
        return {"ok": True}

    @app.post("/login")
    async def log_in(request: Request) -> dict[str, bool]:
        request.session["before"] = 1
        await log_out_elsewhere(request)
        await login(request, SessionUser(user_id="alice"))
        request.session["after"] = 2
        return {"ok": True}

    return app


async def test_rotate_session_id_refuses_a_session_logged_out_meanwhile(
    manager: SessionManager, config: SessionConfig
) -> None:
    SessionManagerProxy.set(manager)
    session, token = await manager.create_session(SessionUser(user_id="alice"))
    client = TestClient(_rotating_app(manager, config))
    client.cookies.set(config.cookie_name, token)

    response = client.post("/sudo")

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert await manager.backend.get_all_keys() == []
    assert await manager._load_session(session.session_id) is None


async def test_a_refused_rotation_does_not_send_a_renewed_token(
    renewing: SessionManager, renewing_config: SessionConfig
) -> None:
    """The winner of a concurrent rotation may already hold the new cookie."""
    SessionManagerProxy.set(renewing)
    _session, token = await renewing.create_session(SessionUser(user_id="alice"))
    client = TestClient(_rotating_app(renewing, renewing_config))
    client.cookies.set(renewing_config.cookie_name, token)

    response = client.post("/sudo-quiet")

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("transport", ["cookie", "header"])
async def test_login_starts_a_new_session_when_the_loaded_one_ended_meanwhile(
    manager: SessionManager, config: SessionConfig, transport: str
) -> None:
    session, token = await manager.create_anonymous_session(cart=["book"])
    client = TestClient(_rotating_app(manager, config))
    if transport == "cookie":
        client.cookies.set(config.cookie_name, token)
        response = client.post("/login")
    else:
        response = client.post("/login", headers=_auth(transport, config, token))

    assert response.status_code == 200
    logged_in, _ = await manager.get_session(_token_sent(response, transport, config))
    assert logged_in.session_id != session.session_id
    assert logged_in.user is not None
    assert logged_in.user.user_id == "alice"
    assert logged_in.data == {"after": 2}
    assert await manager.backend.get_all_keys() == [
        manager._get_backend_key(logged_in.session_id)
    ]


async def test_rotate_session_id_refuses_an_ended_session_without_the_middleware(
    manager: SessionManager,
) -> None:
    """A session put on ``request.state`` by hand is refused the same way."""
    SessionManagerProxy.set(manager)
    stale, _other, _token = await _two_copies(manager)
    await manager.delete_session(stale.session_id)
    app = FastAPI()
    setattr(app.state, "__fastapi_cachex_session_manager", manager)

    @app.post("/sudo")
    async def sudo(request: Request) -> dict[str, bool]:
        setattr(request.state, "__fastapi_cachex_session", stale)
        return {"rotated": await rotate_session_id(request)}

    response = TestClient(app).post("/sudo")

    assert response.status_code == 401
