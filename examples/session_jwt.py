"""Sessions carried as JWTs, for API clients.

With ``token_format="jwt"`` the session token is a signed JWT. The session
itself still lives in the backend, so logging out revokes the token at once.
The client sends it as ``Authorization: Bearer <token>``.

Needs the ``jwt`` extra: ``uv add "fastapi-cachex[jwt]"``.
Run it from a checkout (see ``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/session_jwt.py
"""

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel

from fastapi_cachex import BackendProxy
from fastapi_cachex import FastAPICacheXSessionMiddleware
from fastapi_cachex import SessionConfig
from fastapi_cachex import SessionManager
from fastapi_cachex import SessionManagerProxy
from fastapi_cachex import SessionUser
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.dependencies import ClientIPDep

backend = MemoryBackend()
BackendProxy.set(backend)

config = SessionConfig(
    # HS256 wants a key of at least 32 bytes (HS384: 48, HS512: 64), or
    # SessionManager warns. Set a real random value in production, e.g.
    # `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
    secret_key=os.environ.get(
        "SESSION_SECRET_KEY", "dev-only-placeholder-change-me-before-deploying"
    ),
    token_format="jwt",
    jwt_algorithm="HS256",
    # Optional: issued as `iss`/`aud` and checked on every request.
    jwt_issuer="https://api.example.com",
    jwt_audience="example-clients",
    # The middleware also accepts a cookie. 0.4.0 changes both cookie defaults
    # (to "__Host-session" with the Secure flag), so set them explicitly; over
    # HTTPS use cookie_name="__Host-session" and cookie_https_only=True.
    cookie_name="session",
    cookie_https_only=False,
)
session_manager = SessionManager(backend, config)
# From 0.4.0 ClientIPDep finds the manager only through the proxy; 0.3.9 reads
# the middleware's and warns (FutureWarning) when the proxy holds another one.
SessionManagerProxy.set(session_manager)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Stop the memory backend's cleanup task on shutdown."""
    yield
    await backend.aclose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=session_manager, config=config
)

# Demo accounts only. Store password hashes (argon2, bcrypt) in a real app.
DEMO_USERS = {"alice": "alice-demo-password"}


class Credentials(BaseModel):
    """Login form."""

    username: str
    password: str


@app.post("/token")
async def issue_token(
    credentials: Credentials, client_ip: ClientIPDep
) -> dict[str, str]:
    """Check the password and return a bearer token for a new session."""
    expected = DEMO_USERS.get(credentials.username)
    if expected is None or not secrets.compare_digest(credentials.password, expected):
        raise HTTPException(status_code=401, detail="Wrong username or password")
    _, token = await session_manager.create_session(
        user=SessionUser(user_id=credentials.username),
        ip_address=client_ip,
    )
    return {"access_token": token, "token_type": "bearer"}


@app.get("/me")
async def me(session: AuthenticatedSession) -> dict[str, str]:
    """Requires ``Authorization: Bearer <token>`` for a session with a user."""
    assert session.user is not None  # guaranteed by AuthenticatedSession  # noqa: S101
    return {"user": session.user.user_id}


@app.post("/logout")
async def logout(request: Request) -> dict[str, bool]:
    """Delete the session: the JWT stops working even before it expires."""
    request.session.clear()
    return {"logged_out": True}
