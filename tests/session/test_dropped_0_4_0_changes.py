"""Session changes 0.3.9 announced for 0.4.0 and dropped with #420.

Sessions are deprecated and leave in 0.5.0, so 0.4.0 keeps 0.3.9's behaviour
and no longer warns about these changes:

- #131: ``get_session_manager`` keeps returning the middleware's manager.
- #75: the cookie is read whether or not ``token_source_priority`` lists it.
- #377: ``SessionConfig.use_bearer_token`` stays deprecated, now until 0.5.0.
"""

import warnings

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import FastAPICacheXSessionMiddleware
from fastapi_cachex.session import SessionConfig
from fastapi_cachex.session import SessionManager
from fastapi_cachex.session.dependencies import ClientIPDep
from fastapi_cachex.session.dependencies import OptionalSession
from fastapi_cachex.session.dependencies import SessionManagerDep
from fastapi_cachex.session.dependencies import rotate_session_id
from fastapi_cachex.session.models import SessionUser
from fastapi_cachex.session.proxy import SessionManagerProxy

SECRET = "a" * 32


def _middleware(config: SessionConfig) -> FastAPICacheXSessionMiddleware:
    manager = SessionManager(MemoryBackend(), config)
    return FastAPICacheXSessionMiddleware(FastAPI(), session_manager=manager)


# --- #131: get_session_manager keeps the middleware's manager -----------------


def _manager_app(manager: SessionManager, config: SessionConfig) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/manager")
    async def read_manager(mgr: SessionManagerDep) -> dict[str, bool]:
        return {"is_same": mgr is manager}

    @app.get("/ip")
    async def read_ip(client_ip: ClientIPDep) -> dict[str, str | None]:
        return {"ip": client_ip}

    @app.post("/rotate")
    async def rotate(request: Request) -> dict[str, bool]:
        return {"rotated": await rotate_session_id(request)}

    return app


@pytest.mark.parametrize("proxy", ["empty", "other"])
def test_get_session_manager_returns_the_middleware_manager_silently(
    manager: SessionManager, config: SessionConfig, proxy: str
) -> None:
    """Whatever the proxy holds, 0.4.0 answers with the middleware's manager."""
    if proxy == "other":
        SessionManagerProxy.set(SessionManager(MemoryBackend(), config))
    client = TestClient(_manager_app(manager, config))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        response = client.get("/manager")
        ip = client.get("/ip")
        rotated = client.post("/rotate")

    assert response.json() == {"is_same": True}
    assert ip.status_code == 200
    assert rotated.json() == {"rotated": False}


def test_middleware_from_the_proxy_is_silent(config: SessionConfig) -> None:
    """The recommended wiring: the middleware picks the manager up from the proxy."""
    manager = SessionManager(MemoryBackend(), config)
    SessionManagerProxy.set(manager)
    app = FastAPI()
    app.add_middleware(FastAPICacheXSessionMiddleware)

    @app.get("/manager")
    async def read_manager(mgr: SessionManagerDep) -> dict[str, bool]:
        return {"is_same": mgr is manager}

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        response = TestClient(app).get("/manager")

    assert response.json() == {"is_same": True}


# --- #75: the cookie is read whether or not the list names it ------------------

_COOKIE = {"cookie_name": "session", "cookie_https_only": False}


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"token_source_priority": ["header", "bearer"]},
        {"token_source_priority": ["header"]},
        {"token_source_priority": []},
        {"token_source_priority": ["header", "bearer", "cookie"]},
    ],
)
def test_middleware_is_silent_with_or_without_cookie(
    settings: dict[str, object],
) -> None:
    config = SessionConfig(secret_key=SECRET, **settings, **_COOKIE)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _middleware(config)


@pytest.mark.parametrize(
    "priority", [["cookie", "header"], ["header", "cookie", "bearer"]]
)
def test_cookie_is_accepted_only_as_the_last_source(priority: list[str]) -> None:
    with pytest.raises(ValidationError, match="must be the last entry"):
        SessionConfig(secret_key=SECRET, token_source_priority=priority)


async def test_the_cookie_is_read_with_or_without_the_entry() -> None:
    """Listing "cookie" last is the order it is read in; leaving it out changes nothing."""
    backend = MemoryBackend()
    for priority in (["header", "cookie"], ["header"]):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            config = SessionConfig(
                secret_key=SECRET, token_source_priority=priority, **_COOKIE
            )
            manager = SessionManager(backend, config)
            app = FastAPI()
            app.add_middleware(
                FastAPICacheXSessionMiddleware, session_manager=manager, config=config
            )

            @app.get("/")
            async def read(session: OptionalSession) -> dict[str, object]:
                return {
                    "user": session.user.user_id if session and session.user else None
                }

            _session, token = await manager.create_session(
                user=SessionUser(user_id="u")
            )
            client = TestClient(app)
            client.cookies.set("session", token)

            assert client.get("/").json() == {"user": "u"}, priority


# --- #377: use_bearer_token is deprecated until 0.5.0 ----------------------------


@pytest.mark.parametrize("value", [True, False])
def test_passing_use_bearer_token_warns(value: bool) -> None:
    with pytest.warns(DeprecationWarning, match="use_bearer_token") as record:
        SessionConfig(secret_key=SECRET, use_bearer_token=value)

    message = str(record[0].message)
    assert "removed in version 0.5.0" in message
    assert "issues/377" in message


def test_leaving_use_bearer_token_out_is_silent() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SessionConfig(secret_key=SECRET, token_source_priority=["header", "cookie"])
