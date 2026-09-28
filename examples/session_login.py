"""Server-side sessions with ``FastAPICacheXSessionMiddleware``.

A visitor gets an anonymous session as soon as something is written to
``request.session`` (here, a shopping cart). Logging in rotates the session ID
against session fixation and attaches the user, keeping the cart; logging out
deletes the session. The token travels in a cookie, as with Starlette's
``SessionMiddleware``; header and ``Authorization: Bearer`` tokens work too.

Run it from a checkout (see ``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/session_login.py
"""

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from pydantic import BaseModel

from fastapi_cachex import BackendProxy
from fastapi_cachex import FastAPICacheXSessionMiddleware
from fastapi_cachex import SessionConfig
from fastapi_cachex import SessionManager
from fastapi_cachex import SessionUser
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.session import rotate_session_id
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.dependencies import ClientIPDep
from fastapi_cachex.session.dependencies import OptionalSession

backend = MemoryBackend()
BackendProxy.set(backend)

config = SessionConfig(
    # At least 32 characters. Set a real random value in production, e.g.
    # `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
    secret_key=os.environ.get(
        "SESSION_SECRET_KEY", "dev-only-placeholder-change-me-before-deploying"
    ),
    session_ttl=3600,
    # Keep False only for local HTTP development.
    cookie_https_only=False,
)
session_manager = SessionManager(backend, config)


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


@app.post("/cart/{item}")
async def add_to_cart(item: str, request: Request) -> dict[str, list[str]]:
    """Writing to ``request.session`` starts an anonymous session if needed."""
    cart = [*request.session.get("cart", []), item]
    request.session["cart"] = cart
    return {"cart": cart}


@app.post("/login")
async def login(
    credentials: Credentials,
    request: Request,
    response: Response,
    client_ip: ClientIPDep,
    session: OptionalSession,
) -> dict[str, str]:
    """Check the password, then attach the user under a new session ID."""
    expected = DEMO_USERS.get(credentials.username)
    if expected is None or not secrets.compare_digest(credentials.password, expected):
        raise HTTPException(status_code=401, detail="Wrong username or password")
    user = SessionUser(user_id=credentials.username, username=credentials.username)

    if session is not None:
        # The visitor already has a session (their cart). Give it a new ID so a
        # token planted before login is worthless, then attach the user. The
        # middleware sends the new token in place of the old one.
        await rotate_session_id(request)
        session.user = user
        await session_manager.update_session(session)
        return {"user": user.user_id}

    # No session yet, so the middleware has no token to send: create the
    # session with the user and deliver the token ourselves, with the cookie
    # attributes and cache headers the middleware would use.
    _, token = await session_manager.create_session(
        user=user,
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        config.cookie_name,
        token,
        max_age=config.cookie_max_age,
        path=config.cookie_path,
        domain=config.cookie_domain,
        secure=config.cookie_https_only,
        httponly=True,
        samesite=config.cookie_same_site,
    )
    response.headers[config.header_name] = token  # for header and API clients
    # The token is a credential: no shared cache may store this response.
    response.headers["Cache-Control"] = "private, no-store"
    return {"user": user.user_id}


@app.get("/me")
async def me(session: AuthenticatedSession) -> dict[str, object]:
    """Only for logged-in users; an anonymous session gets ``401`` too."""
    assert session.user is not None  # guaranteed by AuthenticatedSession  # noqa: S101
    return {"user": session.user.user_id, "cart": session.data.get("cart", [])}


@app.post("/logout")
async def logout(request: Request) -> dict[str, bool]:
    """``clear()`` deletes the session and expires the cookie."""
    request.session.clear()
    return {"logged_out": True}
