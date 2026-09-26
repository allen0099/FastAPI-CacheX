"""The STATE.md quick start: a state bound to a nonce cookie (#226)."""

import secrets
from urllib.parse import parse_qs
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.state import StateError
from fastapi_cachex.state import StateManager

COOKIE = "oauth_binding"


def _app() -> FastAPI:
    app = FastAPI()
    states = StateManager(backend=MemoryBackend())

    @app.get("/login")
    async def login():
        nonce = secrets.token_urlsafe(32)
        state = await states.create_state(binding=nonce)
        response = RedirectResponse(
            f"https://provider.example.com/authorize?state={state}"
        )
        response.set_cookie(COOKIE, nonce, max_age=600, httponly=True, samesite="lax")
        return response

    @app.get("/callback")
    async def callback(request: Request, state: str, code: str):
        try:
            await states.consume_state(state, binding=request.cookies.get(COOKIE))
        except StateError as e:
            raise HTTPException(status_code=400, detail="Invalid state") from e
        return {"logged_in_with_code": code}

    return app


def _start(client: TestClient) -> str:
    response = client.get("/login", follow_redirects=False)
    location: str = response.headers["location"]
    return parse_qs(urlparse(location).query)["state"][0]


def test_the_browser_that_started_the_flow_completes_it() -> None:
    client = TestClient(_app())
    state = _start(client)

    response = client.get("/callback", params={"state": state, "code": "c"})

    assert response.status_code == 200


def test_a_state_started_by_an_attacker_is_rejected_in_the_victims_browser() -> None:
    """The login CSRF from #226: before binding, the victim was logged in as the attacker."""
    app = _app()
    attacker, victim = TestClient(app), TestClient(app)
    state = _start(attacker)
    _start(victim)  # the victim has a nonce cookie of their own

    response = victim.get(
        "/callback", params={"state": state, "code": "ATTACKERS_CODE"}
    )

    assert response.status_code == 400
