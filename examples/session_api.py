"""Server-side sessions for an API client that keeps its own token.

``/login`` returns the session token in the response body; the client stores
it and sends it back as ``Authorization: Bearer <token>`` or in the
``X-Session-Token`` header. ``/profile`` needs a logged-in user, ``/public``
works with or without a session, and ``/logout`` deletes the session so the
token stops working. For browsers, prefer the HttpOnly cookie of
``session_login.py``.

Run it from a checkout (see ``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/session_api.py
"""

import os
import secrets
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel

from fastapi_cachex import BackendProxy
from fastapi_cachex import SessionManagerProxy
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.session import FastAPICacheXSessionMiddleware
from fastapi_cachex.session import SessionConfig
from fastapi_cachex.session import SessionManager
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.dependencies import OptionalSession
from fastapi_cachex.session.dependencies import SessionDep

backend = MemoryBackend()
BackendProxy.set(backend)


def session_secret_key() -> str:
    """Return SESSION_SECRET_KEY, or a random key for this run with a warning."""
    key = os.environ.get("SESSION_SECRET_KEY")
    if key:
        return key
    warnings.warn(
        "SESSION_SECRET_KEY is not set, so this run signs sessions with a "
        "random key: they end when the process restarts and are not shared "
        "between workers. Set SESSION_SECRET_KEY to a random value of at least "
        "32 characters, e.g. the output of "
        '`python -c "import secrets; print(secrets.token_urlsafe(48))"`.',
        UserWarning,
        stacklevel=2,
    )
    return secrets.token_urlsafe(48)


config = SessionConfig(
    # At least 32 characters, from the environment; see session_secret_key().
    secret_key=session_secret_key(),
    session_ttl=3600,  # 1 hour
    # Both cookie defaults change in 0.4.0, so set them explicitly. Over HTTPS
    # use cookie_name="__Host-session", cookie_https_only=True.
    cookie_name="session",
    cookie_https_only=False,
)
session_manager = SessionManager(backend, config)
# Register it on the proxy too: from 0.4.0, get_session_manager (and
# SessionManagerDep, ClientIPDep) find the manager there only.
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
    """Login form.

    Credentials arrive in the JSON request body, never in the query string,
    which ends up in browser history and access logs.
    """

    username: str
    password: str


@app.post("/login")
async def login(credentials: Credentials) -> dict[str, str]:
    """Check the password and return the token of a new session."""
    expected = DEMO_USERS.get(credentials.username)
    if expected is None or not secrets.compare_digest(credentials.password, expected):
        raise HTTPException(status_code=401, detail="Wrong username or password")
    user = SessionUser(
        user_id=credentials.username, username=credentials.username, roles=["user"]
    )
    _, token = await session_manager.create_session(user=user)
    # The client stores the token and sends it on later requests in the
    # Authorization or X-Session-Token header.
    return {"token": token}


@app.get("/profile")
async def get_profile(session: AuthenticatedSession) -> dict[str, object]:
    """Requires a session with a user; ``401`` otherwise, anonymous ones included."""
    assert session.user is not None  # guaranteed by AuthenticatedSession  # noqa: S101
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
        "roles": session.user.roles,
    }


@app.get("/public")
async def public_endpoint(session: OptionalSession) -> dict[str, str]:
    """Answers with or without a session."""
    if session is not None and session.user is not None:
        return {"message": f"Hello, {session.user.username}!"}
    return {"message": "Hello, guest!"}


@app.post("/logout")
async def logout(session: SessionDep) -> dict[str, bool]:
    """Delete the session: its token stops working at once."""
    await session_manager.delete_session(session.session_id)
    return {"logged_out": True}
