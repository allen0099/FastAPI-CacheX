"""JWT sessions with custom claims, checked on every request.

``CustomClaimsJWTSerializer`` issues and verifies the same claims as the
built-in JWT serializer and leaves two hooks for extra ones.
``MultiTenantJWTSerializer`` uses them to put ``tenant_id`` and ``api_version``
into every token and to reject tokens issued for another tenant. The serializer
is handed to ``SessionManager`` through ``token_serializer``.

Needs the ``jwt`` extra: ``uv add "fastapi-cachex[jwt]"``. Any backend works;
see ``redis_backend.py`` for Redis. Run it from a checkout (see
``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/session_jwt_claims.py
"""

# --8<-- [start:serializer]
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone
from typing import Any

import jwt
from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel

from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.session import FastAPICacheXSessionMiddleware
from fastapi_cachex.session import SessionConfig
from fastapi_cachex.session import SessionManager
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.models import SessionToken


class CustomClaimsJWTSerializer:
    """JWT serializer with the built-in claims plus extra ones from subclasses."""

    # Claims that from_string() requires besides sid, iat and exp.
    required_claims: tuple[str, ...] = ()

    def __init__(self, config: SessionConfig) -> None:
        """Copy the JWT settings from the public ``SessionConfig`` fields."""
        self.secret = config.secret_key.get_secret_value()
        self.algorithm = config.jwt_algorithm  # must be HS256, HS384 or HS512
        self.issuer = config.jwt_issuer
        self.audience = config.jwt_audience
        self.leeway = config.jwt_leeway
        self.session_ttl = config.session_ttl

    def extra_claims(self, token: SessionToken) -> dict[str, Any]:  # noqa: ARG002
        """Return the claims to add to a new token."""
        return {}

    def check_claims(self, payload: dict[str, Any]) -> None:
        """Raise ValueError if the extra claims of a verified token are wrong."""

    def to_string(self, token: SessionToken) -> str:
        """Encode a SessionToken as a signed JWT."""
        iat = int(token.issued_at.timestamp())
        if token.expires_at is not None:
            exp = int(token.expires_at.timestamp())
        else:
            exp = iat + self.session_ttl

        payload: dict[str, Any] = {"sid": token.session_id, "iat": iat, "exp": exp}
        if self.issuer:
            payload["iss"] = self.issuer
        if self.audience:
            payload["aud"] = self.audience
        payload.update(self.extra_claims(token))
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def from_string(self, token_str: str) -> SessionToken:
        """Verify a JWT and turn it back into a SessionToken."""
        try:
            payload = jwt.decode(
                token_str,
                self.secret,
                algorithms=[self.algorithm],
                issuer=self.issuer,
                audience=self.audience,
                leeway=self.leeway,
                options={"require": ["sid", "iat", "exp", *self.required_claims]},
            )
        except jwt.InvalidTokenError as e:
            msg = "Invalid JWT token"
            raise ValueError(msg) from e

        self.check_claims(payload)
        issued_at = datetime.fromtimestamp(int(payload["iat"]), tz=timezone.utc)
        return SessionToken(
            session_id=str(payload["sid"]), signature="", issued_at=issued_at
        )


# --8<-- [end:serializer]


# --8<-- [start:multi-tenant]
class MultiTenantJWTSerializer(CustomClaimsJWTSerializer):
    """Adds tenant_id and api_version, and rejects tokens for other tenants."""

    required_claims = ("tenant_id", "api_version")

    def __init__(
        self, config: SessionConfig, tenant_id: str, api_version: str = "v1"
    ) -> None:
        """Issue and accept tokens for one tenant and API version."""
        super().__init__(config)
        self.tenant_id = tenant_id
        self.api_version = api_version

    def extra_claims(self, token: SessionToken) -> dict[str, Any]:  # noqa: ARG002
        """Put the tenant and API version into every new token."""
        return {"tenant_id": self.tenant_id, "api_version": self.api_version}

    def check_claims(self, payload: dict[str, Any]) -> None:
        """Reject a token for another tenant or API version."""
        if payload["tenant_id"] != self.tenant_id:
            msg = f"Invalid tenant_id: expected {self.tenant_id}, got {payload['tenant_id']}"
            raise ValueError(msg)
        if payload["api_version"] != self.api_version:
            msg = f"Unsupported API version: {payload['api_version']}"
            raise ValueError(msg)


# --8<-- [end:multi-tenant]

# --8<-- [start:setup]
backend = MemoryBackend()
config = SessionConfig(
    # HS256 wants a key of at least 32 bytes (HS384: 48, HS512: 64). Set a real
    # random value in production, e.g.
    # `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
    secret_key=os.environ.get(
        "SESSION_SECRET_KEY", "dev-only-placeholder-change-me-before-deploying"
    ),
    token_format="jwt",
    jwt_algorithm="HS256",
    jwt_issuer="acme-corp",
    jwt_audience="acme-api",
    cookie_name="__Host-session",  # the middleware also reads a cookie
    cookie_https_only=True,
)

# token_serializer replaces the serializer chosen from token_format.
serializer = MultiTenantJWTSerializer(config, tenant_id="acme-corp", api_version="v2")
session_manager = SessionManager(backend, config, token_serializer=serializer)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Stop the memory backend's cleanup task on shutdown."""
    yield
    await backend.aclose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=session_manager, config=config
)
# --8<-- [end:setup]

# Demo accounts only. Store password hashes (argon2, bcrypt) in a real app.
DEMO_USERS = {"alice": "alice-demo-password"}


class Credentials(BaseModel):
    """Login form."""

    username: str
    password: str


@app.post("/auth/login")
async def login(credentials: Credentials) -> dict[str, str]:
    """Return a JWT that carries the tenant_id and api_version claims."""
    expected = DEMO_USERS.get(credentials.username)
    if expected is None or not secrets.compare_digest(credentials.password, expected):
        raise HTTPException(status_code=401, detail="Wrong username or password")
    user = SessionUser(user_id=credentials.username, username=credentials.username)
    _, token = await session_manager.create_session(user=user)
    return {"token": token, "token_type": "bearer", "tenant_id": serializer.tenant_id}


@app.get("/api/profile")
async def get_profile(session: AuthenticatedSession) -> dict[str, str | None]:
    """Protected endpoint; the tenant was checked while decoding the JWT.

    A token for another tenant fails to decode, so the middleware loads no
    session and ``AuthenticatedSession`` answers ``401``.
    """
    assert session.user is not None  # guaranteed by AuthenticatedSession  # noqa: S101
    return {"user_id": session.user.user_id, "username": session.user.username}
