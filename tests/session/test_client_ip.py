"""Tests for the public client-IP helpers used with session IP binding."""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import HTTPConnection

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import FastAPICacheXSessionMiddleware
from fastapi_cachex.session import SessionConfig
from fastapi_cachex.session import SessionManager
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session import get_client_ip
from fastapi_cachex.session.dependencies import ClientIPDep
from fastapi_cachex.session.dependencies import OptionalSession
from fastapi_cachex.session.dependencies import SessionManagerDep


def _connection(peer: str | None, headers: dict[str, str]) -> HTTPConnection:
    return HTTPConnection(
        {
            "type": "http",
            "client": (peer, 1234) if peer is not None else None,
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def test_get_client_ip_uses_forwarded_address_behind_trusted_proxy():
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.9"])
    connection = _connection("10.0.0.9", {"X-Forwarded-For": "198.51.100.5"})

    assert get_client_ip(connection, config) == "198.51.100.5"


def test_get_client_ip_ignores_forwarded_address_from_untrusted_peer():
    config = SessionConfig(secret_key="a" * 32)
    connection = _connection("10.0.0.9", {"X-Forwarded-For": "198.51.100.5"})

    assert get_client_ip(connection, config) == "10.0.0.9"


def test_get_client_ip_without_peer():
    config = SessionConfig(secret_key="a" * 32)

    assert get_client_ip(_connection(None, {}), config) is None


@pytest.fixture
def proxied_app() -> FastAPI:
    """An app with IP binding whose only peer (TestClient) is a trusted proxy."""
    config = SessionConfig(
        secret_key="a" * 32, ip_binding=True, trusted_proxies=["testclient"]
    )
    manager = SessionManager(MemoryBackend(), config)

    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.post("/login")
    async def login(
        manager: SessionManagerDep, client_ip: ClientIPDep
    ) -> dict[str, str | None]:
        session, token = await manager.create_session(
            user=SessionUser(user_id="u1"), ip_address=client_ip
        )
        return {"token": token, "ip": session.ip_address}

    @app.post("/login-peer")
    async def login_peer(
        request: Request, manager: SessionManagerDep
    ) -> dict[str, str | None]:
        _, token = await manager.create_session(
            user=SessionUser(user_id="u1"),
            ip_address=request.client.host if request.client else None,
        )
        return {"token": token}

    @app.get("/me")
    async def me(session: OptionalSession) -> dict[str, bool]:
        return {"authenticated": session is not None}

    return app


def test_client_ip_dep_binds_the_address_the_middleware_checks(proxied_app: FastAPI):
    client = TestClient(proxied_app)
    forwarded = {"X-Forwarded-For": "198.51.100.5"}

    login = client.post("/login", headers=forwarded).json()

    assert login["ip"] == "198.51.100.5"
    me = client.get("/me", headers={**forwarded, "X-Session-Token": login["token"]})
    assert me.json() == {"authenticated": True}


def test_peer_address_binding_fails_behind_trusted_proxy(proxied_app: FastAPI):
    """The problem ClientIPDep solves: the proxy's address never matches."""
    client = TestClient(proxied_app)
    forwarded = {"X-Forwarded-For": "198.51.100.5"}

    token = client.post("/login-peer", headers=forwarded).json()["token"]

    me = client.get("/me", headers={**forwarded, "X-Session-Token": token})
    assert me.json() == {"authenticated": False}


@pytest.mark.parametrize(
    ("entry", "peer"),
    [
        ("10.0.0.0/8", "10.20.30.40"),
        ("10.0.0.8", "10.0.0.8"),
        ("10.0.0.8/24", "10.0.0.200"),  # host bits are ignored
        ("2001:db8::/32", "2001:db8:1::5"),
        ("2001:db8::1", "2001:DB8::1"),
        ("10.0.0.0/8", "::ffff:10.1.2.3"),  # dual-stack socket
        ("testclient", "testclient"),
    ],
)
def test_trusted_proxy_matches(entry: str, peer: str):
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=[entry])

    assert config.is_trusted_proxy(peer)


@pytest.mark.parametrize(
    ("entry", "peer"),
    [
        ("10.0.0.0/8", "11.0.0.1"),
        ("10.0.0.8", "10.0.0.9"),
        ("2001:db8::/32", "2001:db9::1"),
        ("10.0.0.0/8", "2001:db8::1"),
        ("10.0.0.0/8", "testclient"),
        ("testclient", "10.0.0.1"),
    ],
)
def test_trusted_proxy_rejects(entry: str, peer: str):
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=[entry])

    assert not config.is_trusted_proxy(peer)


@pytest.mark.parametrize("entry", ["10.0.0.0/33", "10.0.0.0/x", "proxy/8"])
def test_malformed_cidr_entry_is_rejected(entry: str):
    with pytest.raises(ValidationError, match="not a valid CIDR range"):
        SessionConfig(secret_key="a" * 32, trusted_proxies=[entry])


def test_forwarded_chain_skips_every_hop_inside_a_trusted_range():
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.0/8"])
    connection = _connection(
        "10.0.0.9", {"X-Forwarded-For": "198.51.100.66, 203.0.113.5, 10.1.1.1"}
    )

    assert get_client_ip(connection, config) == "203.0.113.5"


def test_model_copy_update_uses_the_new_ranges():
    """model_copy(update=...) skips validation; matching must not be stale."""
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.0/8"])
    copied = config.model_copy(update={"trusted_proxies": ["192.168.0.0/16"]})

    assert copied.is_trusted_proxy("192.168.1.1")
    assert not copied.is_trusted_proxy("10.0.0.1")
