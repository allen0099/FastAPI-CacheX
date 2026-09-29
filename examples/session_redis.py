"""Server-side sessions on Redis, with bindings, flash messages and logout everywhere.

The API client gets its token from ``/api/auth/login`` and sends it back as
``Authorization: Bearer <token>`` or in the ``X-Session-Token`` header. The
session is bound to the client's IP address, slides forward while in use, and
carries flash messages between requests. ``/api/auth/logout-all`` ends every
session of the user, on every device.

Needs the ``redis`` extra (``uv add "fastapi-cachex[redis]"``) and a Redis
server, configured from ``REDIS_HOST``, ``REDIS_PORT``, ``REDIS_DB`` and
``REDIS_PASSWORD`` as in ``redis_backend.py``. Run it from a checkout (see
``examples/README.md``)::

    REDIS_PORT=6379 uv run --with "fastapi-cli[standard]" fastapi dev examples/session_redis.py
"""

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.session import FastAPICacheXSessionMiddleware
from fastapi_cachex.session import SessionConfig
from fastapi_cachex.session import SessionManager
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.dependencies import ClientIPDep
from fastapi_cachex.session.dependencies import SessionDep

# The client connects lazily, so creating it at import needs no server yet.
backend = AsyncRedisCacheBackend(
    host=os.environ.get("REDIS_HOST", "127.0.0.1"),
    port=int(os.environ.get("REDIS_PORT", "6379")),
    db=int(os.environ.get("REDIS_DB", "0")),
    password=os.environ.get("REDIS_PASSWORD") or None,
    # Namespaces this app's keys on a shared server.
    key_prefix="fastapi_cachex_example:",
)

config = SessionConfig(
    # At least 32 characters. Set a real random value in production, e.g.
    # `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
    secret_key=os.environ.get(
        "SESSION_SECRET_KEY", "dev-only-placeholder-change-me-before-deploying"
    ),
    session_ttl=3600,
    sliding_expiration=True,
    sliding_threshold=0.5,
    ip_binding=True,  # reject the token from another IP address
    user_agent_binding=False,  # optional: reject it from another User-Agent
)
session_manager = SessionManager(backend, config)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Close the Redis connection pool on shutdown."""
    try:
        yield
    finally:
        await backend.aclose()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=session_manager, config=config
)

# Demo accounts only. Store password hashes (argon2, bcrypt) in a real app,
# and read the user's ID and roles from your database.
DEMO_USERS = {"alice": "alice-demo-password", "admin": "admin-demo-password"}
DEMO_ROLES = {"alice": ["user"], "admin": ["admin", "user"]}


class Credentials(BaseModel):
    """Login form."""

    username: str
    password: str


@app.post("/api/auth/login")
async def login(
    credentials: Credentials, request: Request, client_ip: ClientIPDep
) -> dict[str, object]:
    """Check the password and return the token of a new, bound session."""
    expected = DEMO_USERS.get(credentials.username)
    if expected is None or not secrets.compare_digest(credentials.password, expected):
        raise HTTPException(status_code=401, detail="Wrong username or password")

    user = SessionUser(
        user_id=f"user_{credentials.username}",
        username=credentials.username,
        email=f"{credentials.username}@example.com",
        roles=DEMO_ROLES[credentials.username],
    )
    # `client_ip` is the address the middleware checks on later requests,
    # including behind trusted proxies.
    session, token = await session_manager.create_session(
        user=user,
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )

    session.add_flash_message("Login successful!", "success")
    # Changes to a Session object are saved only by update_session().
    await session_manager.update_session(session)

    return {
        "token": token,  # the client stores it and sends it on later requests
        "user": {"username": user.username, "roles": user.roles},
    }


@app.get("/api/user/profile")
async def get_user_profile(session: AuthenticatedSession) -> dict[str, object]:
    """Return the user's profile (requires a logged-in user)."""
    assert session.user is not None  # guaranteed by AuthenticatedSession  # noqa: S101
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
        "email": session.user.email,
        "roles": session.user.roles,
        "session_created": session.created_at.isoformat(),
        "last_accessed": session.last_accessed.isoformat(),
    }


@app.post("/api/user/update")
async def update_user_profile(
    email: str, session: AuthenticatedSession
) -> dict[str, str]:
    """Change the email stored in the session."""
    assert session.user is not None  # guaranteed by AuthenticatedSession  # noqa: S101
    session.user.email = email
    session.data["last_updated"] = datetime.now(timezone.utc).isoformat()
    await session_manager.update_session(session)
    return {"message": "Profile updated"}


@app.get("/api/messages")
async def get_flash_messages(session: SessionDep) -> dict[str, object]:
    """Return the flash messages once."""
    messages = session.get_flash_messages(clear=True)
    # Clearing only changes the in-memory object; save it so the messages
    # are not shown again on the next request.
    await session_manager.update_session(session)
    return {"messages": messages}


@app.post("/api/auth/logout")
async def logout(session: SessionDep) -> dict[str, str]:
    """End this session; the client should discard its token."""
    await session_manager.delete_session(session.session_id)
    return {"message": "Logged out"}


@app.post("/api/auth/logout-all")
async def logout_all_devices(session: AuthenticatedSession) -> dict[str, str]:
    """End every session of this user, on every device."""
    assert session.user is not None  # guaranteed by AuthenticatedSession  # noqa: S101
    count = await session_manager.delete_user_sessions(session.user.user_id)
    return {"message": f"Logged out from {count} devices"}
