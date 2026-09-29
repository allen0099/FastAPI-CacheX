"""Tests for session middleware and token extraction."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import get_optional_session
from fastapi_cachex.session.dependencies import rotate_session_id
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import SessionMiddleware
from fastapi_cachex.session.middleware import _extract_header_token
from fastapi_cachex.session.middleware import get_client_ip
from fastapi_cachex.session.models import Session
from fastapi_cachex.session.models import SessionUser
from fastapi_cachex.session.proxy import SessionManagerProxy

# The deprecated `SessionMiddleware` is exercised the way an application uses
# it: installed with `add_middleware`, reached over HTTP, and observed through
# `get_optional_session` and the response headers.

_DEPRECATION = "SessionMiddleware is deprecated"


def _client(
    manager: SessionManager | None,
    config: SessionConfig | None = None,
    *,
    peer: str = "testclient",
) -> TestClient:
    """A client for an app behind `SessionMiddleware`, its stack already built.

    Starlette constructs middleware on the first request, so the warm-up
    request below is where the `DeprecationWarning` is raised and expected.
    `/whoami` reports the session the middleware loaded; `/rotate` gives it a
    new ID, as a login handler would.
    """
    app = FastAPI()
    app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

    @app.get("/whoami")
    async def whoami(
        session: Annotated[Session | None, Depends(get_optional_session)],
    ) -> dict[str, str | None]:
        if session is None:
            return {"session_id": None, "user": None}
        return {
            "session_id": session.session_id,
            "user": session.user.user_id if session.user else None,
        }

    @app.post("/rotate")
    async def rotate(request: Request) -> dict[str, bool]:
        return {"rotated": await rotate_session_id(request)}

    client = TestClient(app, client=(peer, 50000))
    with pytest.warns(DeprecationWarning, match=_DEPRECATION):
        client.get("/whoami")
    return client


def test_construction_warns_and_points_to_the_replacement(
    manager: SessionManager,
) -> None:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, session_manager=manager)

    with pytest.warns(DeprecationWarning, match="FastAPICacheXSessionMiddleware"):
        TestClient(app).get("/")


async def test_a_header_token_loads_the_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    session, token = await manager.create_session(user=SessionUser(user_id="u1"))
    client = _client(manager, config)

    response = client.get("/whoami", headers={config.header_name: token})

    assert response.json() == {"session_id": session.session_id, "user": "u1"}
    assert config.header_name not in response.headers


async def test_a_bearer_token_loads_the_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    session, token = await manager.create_session(user=SessionUser(user_id="u1"))
    client = _client(manager, config)

    response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.json() == {"session_id": session.session_id, "user": "u1"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Session-Token": "invalid-token"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer invalid-token"},
    ],
    ids=["no-token", "invalid-header", "empty-bearer", "invalid-bearer"],
)
def test_a_missing_or_invalid_token_loads_no_session(
    manager: SessionManager, config: SessionConfig, headers: dict[str, str]
) -> None:
    """The request still reaches the handler, without a session."""
    client = _client(manager, config)

    response = client.get("/whoami", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"session_id": None, "user": None}
    assert config.header_name not in response.headers


async def test_an_expired_session_is_not_loaded(
    manager: SessionManager, config: SessionConfig
) -> None:
    session, token = await manager.create_session(user=SessionUser(user_id="u1"))
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await manager._save_session(session)
    client = _client(manager, config)

    response = client.get("/whoami", headers={config.header_name: token})

    assert response.status_code == 200
    assert response.json() == {"session_id": None, "user": None}


async def test_config_defaults_to_the_managers() -> None:
    config = SessionConfig(secret_key="a" * 32, header_name="X-Custom-Session")
    manager = SessionManager(MemoryBackend(), config)
    _session, token = await manager.create_session(user=SessionUser(user_id="u1"))
    client = _client(manager)

    response = client.get("/whoami", headers={"X-Custom-Session": token})

    assert response.json()["user"] == "u1"


async def test_an_explicit_config_overrides_the_managers(
    manager: SessionManager,
) -> None:
    override = SessionConfig(secret_key="a" * 32, header_name="X-Other-Session")
    _session, token = await manager.create_session(user=SessionUser(user_id="u1"))
    client = _client(manager, override)

    assert (
        client.get("/whoami", headers={"X-Other-Session": token}).json()["user"] == "u1"
    )
    assert (
        client.get("/whoami", headers={"X-Session-Token": token}).json()["user"] is None
    )


async def test_the_manager_defaults_to_the_proxy(
    manager: SessionManager, config: SessionConfig
) -> None:
    SessionManagerProxy.set(manager)
    _session, token = await manager.create_session(user=SessionUser(user_id="u1"))
    client = _client(None)

    response = client.get("/whoami", headers={config.header_name: token})

    assert response.json()["user"] == "u1"


@pytest.mark.parametrize(
    ("peer", "loaded"), [("203.0.113.7", True), ("198.51.100.1", False)]
)
async def test_ip_binding_checks_the_peer_address(
    config: SessionConfig, peer: str, loaded: bool
) -> None:
    config.ip_binding = True
    manager = SessionManager(MemoryBackend(), config)
    _session, token = await manager.create_session(
        user=SessionUser(user_id="u1"), ip_address="203.0.113.7"
    )
    client = _client(manager, config, peer=peer)

    response = client.get("/whoami", headers={config.header_name: token})

    assert (response.json()["user"] == "u1") is loaded


@pytest.mark.parametrize(
    ("forwarded_for", "loaded"), [("203.0.113.7", True), ("198.51.100.1", False)]
)
async def test_ip_binding_uses_the_forwarded_address_behind_a_trusted_proxy(
    forwarded_for: str, loaded: bool
) -> None:
    config = SessionConfig(
        secret_key="a" * 32, ip_binding=True, trusted_proxies=["10.0.0.9"]
    )
    manager = SessionManager(MemoryBackend(), config)
    _session, token = await manager.create_session(
        user=SessionUser(user_id="u1"), ip_address="203.0.113.7"
    )
    client = _client(manager, config, peer="10.0.0.9")

    response = client.get(
        "/whoami",
        headers={config.header_name: token, "X-Forwarded-For": forwarded_for},
    )

    assert (response.json()["user"] == "u1") is loaded


@pytest.mark.parametrize(
    ("user_agent", "loaded"), [("App/1.0", True), ("Other/2.0", False)]
)
async def test_user_agent_binding_checks_the_request_user_agent(
    config: SessionConfig, user_agent: str, loaded: bool
) -> None:
    config.user_agent_binding = True
    manager = SessionManager(MemoryBackend(), config)
    _session, token = await manager.create_session(
        user=SessionUser(user_id="u1"), user_agent="App/1.0"
    )
    client = _client(manager, config)

    response = client.get(
        "/whoami", headers={config.header_name: token, "User-Agent": user_agent}
    )

    assert (response.json()["user"] == "u1") is loaded


async def test_a_rotated_session_id_is_sent_back_as_a_new_token(
    manager: SessionManager, config: SessionConfig
) -> None:
    SessionManagerProxy.set(manager)
    session, token = await manager.create_session(user=SessionUser(user_id="u1"))
    client = _client(manager, config)

    response = client.post("/rotate", headers={config.header_name: token})

    assert response.json() == {"rotated": True}
    new_token = response.headers[config.header_name]
    rotated = client.get("/whoami", headers={config.header_name: new_token}).json()
    assert rotated["user"] == "u1"
    assert rotated["session_id"] != session.session_id
    old = client.get("/whoami", headers={config.header_name: token}).json()
    assert old["user"] is None


async def test_sliding_expiration_sends_the_renewed_token() -> None:
    """The refreshed token goes back in the response header, with a later expiry."""
    slide_config = SessionConfig(
        secret_key="a" * 32,
        session_ttl=3600,
        sliding_expiration=True,
        sliding_threshold=0.5,
    )
    manager = SessionManager(MemoryBackend(), slide_config)
    created, original_token = await manager.create_session(
        user=SessionUser(user_id="slide-user")
    )

    # Shorten expiry so time_remaining < sliding threshold (< 50% of 3600 s)
    shortened_expiry = datetime.now(timezone.utc) + timedelta(seconds=1000)
    created.expires_at = shortened_expiry
    await manager._save_session(created)
    client = _client(manager, slide_config)

    response = client.get("/whoami", headers={slide_config.header_name: original_token})

    assert response.json()["user"] == "slide-user"
    renewed = response.headers.get(slide_config.header_name)
    assert renewed is not None

    renewed_session, _ = await manager.get_session(renewed)
    assert renewed_session.expires_at is not None
    assert renewed_session.expires_at > shortened_expiry


# `get_client_ip` is the address resolution the middleware binds sessions to.


def test_get_client_ip_ignores_forwarded_headers_by_default(
    config: SessionConfig,
) -> None:
    """Forwarded headers are spoofable, so an untrusted peer's are ignored."""
    connection = _connection(
        {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"}, peer="10.0.0.9"
    )

    assert get_client_ip(connection, config) == "10.0.0.9"


def test_get_client_ip_from_x_forwarded_for_behind_trusted_proxy() -> None:
    """A proxy the app vouches for may report the real client address."""
    config = SessionConfig(
        secret_key="a" * 32, trusted_proxies=["10.0.0.9", "10.0.0.1"]
    )
    connection = _connection(
        {"X-Forwarded-For": "192.168.1.1, 10.0.0.1"}, peer="10.0.0.9"
    )

    assert get_client_ip(connection, config) == "192.168.1.1"


def test_get_client_ip_ignores_a_prepended_forwarded_entry() -> None:
    """Proxies append, so the leftmost entry is whatever the caller sent."""
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.9"])
    # The attacker sent the first entry themselves; nginx appended the second.
    connection = _connection(
        {"X-Forwarded-For": "198.51.100.5, 203.0.113.99"}, peer="10.0.0.9"
    )

    assert get_client_ip(connection, config) == "203.0.113.99"


def test_get_client_ip_falls_back_when_every_hop_is_trusted() -> None:
    """With no untrusted entry left there is no client address to recover."""
    config = SessionConfig(
        secret_key="a" * 32, trusted_proxies=["10.0.0.9", "10.0.0.1"]
    )
    connection = _connection({"X-Forwarded-For": "10.0.0.1"}, peer="10.0.0.9")

    assert get_client_ip(connection, config) == "10.0.0.9"


def test_get_client_ip_from_real_ip_behind_trusted_proxy() -> None:
    """X-Real-IP is the fallback once the peer is trusted."""
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.9"])
    connection = _connection({"X-Real-IP": "192.168.1.1"}, peer="10.0.0.9")

    assert get_client_ip(connection, config) == "192.168.1.1"


def test_get_client_ip_from_client(config: SessionConfig) -> None:
    connection = _connection({}, peer="192.168.1.1")

    assert get_client_ip(connection, config) == "192.168.1.1"


def test_get_client_ip_none(config: SessionConfig) -> None:
    """No peer and no trusted forwarding: there is no address."""
    assert get_client_ip(_connection({}), config) is None


def _connection(headers: dict[str, str], peer: str | None = None) -> Request:
    """A bare `Request` carrying only the headers (and peer) under test."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": (peer, 1234) if peer is not None else None,
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in headers.items()
            ],
        }
    )


def test_bearer_is_used_when_the_header_source_finds_nothing(
    config: SessionConfig,
) -> None:
    """The priority list is a fallback chain, not a first-entry-only lookup.

    Every other extraction test supplies the header it asks for first, so the
    loop always returned on its first pass and an implementation that only
    ever checked `token_source_priority[0]` would have passed them all.
    """
    token = _extract_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )

    assert token == "from-bearer"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer from-bearer",
        "bearer from-bearer",
        "BEARER from-bearer",
        "Bearer   from-bearer",
    ],
)
def test_bearer_scheme_is_matched_case_insensitively(
    config: SessionConfig, authorization: str
) -> None:
    """Auth schemes are case-insensitive (RFC 9110 §11.1), and RFC 6750 allows
    more than one space before the token (#166).
    """
    token = _extract_header_token(_connection({"Authorization": authorization}), config)

    assert token == "from-bearer"


@pytest.mark.parametrize(
    "authorization",
    ["Bearer", "Bearer ", "Bearer   ", "Basic from-bearer", "Bearerfrom-bearer"],
)
def test_an_empty_or_non_bearer_authorization_header_yields_no_token(
    config: SessionConfig, authorization: str
) -> None:
    token = _extract_header_token(_connection({"Authorization": authorization}), config)

    assert token is None


def test_header_wins_over_bearer_when_both_are_present(
    config: SessionConfig,
) -> None:
    """Order in the list is the order that is honoured."""
    token = _extract_header_token(
        _connection(
            {
                config.header_name: "from-header",
                "Authorization": "Bearer from-bearer",
            }
        ),
        config,
    )

    assert token == "from-header"


def test_bearer_source_is_skipped_when_bearer_tokens_are_disabled() -> None:
    """`use_bearer_token=False` must win over the priority list."""
    with pytest.warns(DeprecationWarning, match="use_bearer_token"):
        config = SessionConfig(secret_key="a" * 32, use_bearer_token=False)

    token = _extract_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )

    assert token is None


def test_an_unknown_source_is_skipped_rather_than_read_as_a_bearer_token() -> None:
    """The chain ends in an `elif`, not an `else`, and this is why.

    `token_source_priority` is a list of Literals, so pydantic refuses an
    unknown source at construction and no caller can reach this through the
    public API. Nothing revalidates the list afterwards, though, and the
    branch exists for the maintainer who adds a third source to the Literal
    and forgets this function: it must fall through, not inherit whatever the
    last branch happens to do. Mutating the list in place is the only way to
    stand where that maintainer will stand.
    """
    config = SessionConfig(secret_key="a" * 32)
    config.token_source_priority[:] = ["query"]  # type: ignore[list-item]

    token = _extract_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )

    assert token is None


def test_a_known_source_after_an_unknown_one_is_still_honoured(
    config: SessionConfig,
) -> None:
    """Falling through must continue the chain, not abandon it."""
    config.token_source_priority[:] = ["query", "header"]  # type: ignore[list-item]

    token = _extract_header_token(
        _connection({config.header_name: "from-header"}), config
    )

    assert token == "from-header"
