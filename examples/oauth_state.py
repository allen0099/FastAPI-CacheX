"""One-time OAuth ``state`` values with ``StateManager``.

``/login`` stores a random state bound to a nonce cookie and redirects to the
provider; ``/callback`` consumes it. A state works once, and only in the
browser that started the flow, which is what protects the callback against
CSRF (RFC 6749, section 10.12).

The provider here is a placeholder: this app never talks to it.
Run it from a checkout (see ``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/oauth_state.py
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import RedirectResponse

from fastapi_cachex import BackendProxy
from fastapi_cachex import StateManagerDep
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.state import StateError

backend = MemoryBackend()
BackendProxy.set(backend)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Stop the memory backend's cleanup task on shutdown."""
    yield
    await backend.aclose()


app = FastAPI(lifespan=lifespan)

AUTHORIZE_URL = "https://provider.example.com/authorize"
BINDING_COOKIE = "oauth_binding"


@app.get("/login")
async def login(states: StateManagerDep) -> RedirectResponse:
    """Create a state bound to this browser and send it to the provider."""
    nonce = secrets.token_urlsafe(32)
    state = await states.create_state(
        ttl=600, binding=nonce, metadata={"next": "/dashboard"}
    )
    query = urlencode({"state": state, "client_id": "example-client"})
    response = RedirectResponse(f"{AUTHORIZE_URL}?{query}")
    # Lax, not Strict: the callback is a cross-site navigation from the provider.
    # Browsers accept Secure cookies on http://localhost.
    response.set_cookie(
        BINDING_COOKIE, nonce, max_age=600, httponly=True, secure=True, samesite="lax"
    )
    return response


@app.get("/callback")
async def callback(
    request: Request, state: str, code: str, states: StateManagerDep
) -> RedirectResponse:
    """Accept the state once, and only from the browser that started the flow."""
    try:
        data = await states.consume_state(
            state, binding=request.cookies.get(BINDING_COOKIE)
        )
    except StateError as exc:  # unknown, expired, reused or another browser's
        raise HTTPException(status_code=400, detail="Invalid state") from exc

    # Exchange `code` for tokens at the provider and create a session here.
    _ = code
    response = RedirectResponse(str(data.metadata.get("next", "/")))
    response.delete_cookie(BINDING_COOKIE)
    return response
