"""Security hardening tests for session token handling and client identification."""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.models import SessionUser
from fastapi_cachex.session.security import SecurityManager


@pytest.fixture
def config() -> SessionConfig:
    """Session config with IP binding on, trusting nothing by default."""
    return SessionConfig(secret_key="a" * 32, ip_binding=True)


@pytest.fixture
def manager(config: SessionConfig) -> SessionManager:
    """Session manager over an in-process backend."""
    return SessionManager(MemoryBackend(), config)


def test_non_ascii_signature_is_rejected_not_raised():
    """`hmac.compare_digest` refuses non-ASCII str; that must not escape."""
    security = SecurityManager("a" * 32)

    assert security.verify_signature("session-id", "簽章") is False
    assert security.verify_signature("session-id", "a.\xe9x.1") is False


def test_valid_signature_still_verifies():
    """The bytes comparison must not break the happy path."""
    security = SecurityManager("a" * 32)
    signature = security.sign_session_id("session-id")

    assert security.verify_signature("session-id", signature) is True
    assert security.verify_signature("other-id", signature) is False


def test_non_ascii_token_does_not_crash_the_middleware(
    manager: SessionManager, config: SessionConfig
):
    """An unauthenticated caller must not be able to provoke a 500."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/whoami")
    async def whoami(request: Request) -> dict[str, bool]:
        return {"authenticated": bool(request.scope.get("session"))}

    client = TestClient(app)
    # Sent as raw bytes: Starlette decodes header bytes as latin-1, so a token
    # can carry bytes that httpx would refuse to encode from a str.
    response = client.get(
        "/whoami", headers={b"x-session-token": b"a.\xe9x.1699999999"}
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": False}


def test_forged_forwarded_header_cannot_satisfy_ip_binding(
    manager: SessionManager, config: SessionConfig
):
    """A stolen token plus a forged X-Forwarded-For must not pass IP binding."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/me")
    async def me(request: Request) -> dict[str, bool]:
        return {"authenticated": bool(request.scope.get("session", {}))}

    client = TestClient(app)

    # TestClient presents itself as "testclient"; bind a session to some other
    # address and then try to claim that address via the header.
    import asyncio

    _, token = asyncio.run(
        manager.create_session(
            user=SessionUser(user_id="u1"),
            ip_address="203.0.113.7",
        )
    )

    response = client.get(
        "/me",
        headers={
            "X-Session-Token": token,
            "X-Forwarded-For": "203.0.113.7",
        },
    )

    # The header is ignored, the peer address does not match the binding, and
    # the session is refused rather than honoured.
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}


def test_prepended_forwarded_entry_cannot_satisfy_ip_binding(manager: SessionManager):
    """Behind a trusted proxy, the attacker's own entry must not be believed.

    `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for` appends, so a
    caller who sends the header themselves ends up with their chosen address in
    front of the one the proxy added.
    """
    # The proxy in this test is TestClient itself, which presents as
    # "testclient"; trusting it puts us in the deployment the setting exists for.
    config = SessionConfig(
        secret_key="a" * 32, ip_binding=True, trusted_proxies=["testclient"]
    )
    manager = SessionManager(MemoryBackend(), config)

    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/me")
    async def me(request: Request) -> dict[str, bool]:
        # The backend session is only attached once every binding check passed.
        loaded = request.scope["state"].get("__fastapi_cachex_session")
        return {"authenticated": loaded is not None}

    import asyncio

    _, token = asyncio.run(
        manager.create_session(
            user=SessionUser(user_id="u1"),
            ip_address="198.51.100.5",
        )
    )

    client = TestClient(app)
    forged = client.get(
        "/me",
        headers={
            "X-Session-Token": token,
            # Left entry forged by the attacker, right entry added by the proxy.
            "X-Forwarded-For": "198.51.100.5, 203.0.113.99",
        },
    )

    assert forged.status_code == 200
    assert forged.json() == {"authenticated": False}

    # The genuine client arrives with only the proxy's own entry.
    genuine = client.get(
        "/me",
        headers={"X-Session-Token": token, "X-Forwarded-For": "198.51.100.5"},
    )

    assert genuine.json() == {"authenticated": True}


def test_jwt_algorithm_none_is_rejected():
    """An unsigned JWT would make every session forgeable."""
    with pytest.raises(ValidationError, match="jwt_algorithm must be one of"):
        SessionConfig(secret_key="a" * 32, token_format="jwt", jwt_algorithm="none")


def test_unknown_jwt_algorithm_is_rejected():
    """Typos must fail at config time, not at first decode."""
    with pytest.raises(ValidationError, match="jwt_algorithm must be one of"):
        SessionConfig(secret_key="a" * 32, jwt_algorithm="HS255")


def test_supported_jwt_algorithms_are_accepted():
    """The documented default and the common asymmetric options still work."""
    for algorithm in ("HS256", "HS512", "RS256", "ES256", "EdDSA"):
        config = SessionConfig(secret_key="a" * 32, jwt_algorithm=algorithm)
        assert config.jwt_algorithm == algorithm


def test_trusted_proxies_defaults_to_empty():
    """The safe default is to believe no forwarded headers at all."""
    assert SessionConfig(secret_key="a" * 32).trusted_proxies == []


def test_jwt_leeway_description_does_not_promise_nbf():
    """`nbf` is neither issued nor verified; the field text must say so."""
    description = SessionConfig.model_fields["jwt_leeway"].description or ""

    assert "nbf" in description
    assert "not" in description
