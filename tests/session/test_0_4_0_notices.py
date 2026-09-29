"""Advance notices for the session changes 0.4.0 makes (#352).

- #256: the default session cookie becomes ``__Host-session`` with Secure.
- #131: ``get_session_manager`` resolves through ``SessionManagerProxy`` only.
- #75: a ``token_source_priority`` without ``"cookie"`` disables the cookie.
- #377: ``SessionConfig.use_bearer_token`` is removed.
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

# --- #256: default cookie name and Secure flag ---------------------------------


def _middleware(config: SessionConfig) -> FastAPICacheXSessionMiddleware:
    manager = SessionManager(MemoryBackend(), config)
    return FastAPICacheXSessionMiddleware(FastAPI(), session_manager=manager)


@pytest.mark.parametrize(
    ("settings", "named"),
    [
        ({}, "default cookie_name and cookie_https_only"),
        ({"cookie_https_only": True}, "default cookie_name of"),
        ({"cookie_name": "session"}, "default cookie_https_only of"),
    ],
)
def test_middleware_warns_while_a_cookie_default_is_relied_upon(
    settings: dict[str, object], named: str
) -> None:
    config = SessionConfig(secret_key=SECRET, **settings)

    with pytest.warns(FutureWarning, match=named) as record:
        _middleware(config)

    message = str(record[0].message)
    assert "__Host-session" in message
    assert "cookie_name='session', cookie_https_only=False" in message


@pytest.mark.parametrize(
    "settings",
    [
        {"cookie_name": "session", "cookie_https_only": False},
        {"cookie_name": "__Host-session", "cookie_https_only": True},
        {"cookie_name": "sid", "cookie_https_only": True},
    ],
)
def test_middleware_is_silent_with_explicit_cookie_settings(
    settings: dict[str, object],
) -> None:
    config = SessionConfig(secret_key=SECRET, **settings)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _middleware(config)


def test_middleware_checks_the_config_it_falls_back_to() -> None:
    """With no ``config=``, the manager's config is the one that counts."""
    manager = SessionManager(MemoryBackend(), SessionConfig(secret_key=SECRET))

    with pytest.warns(FutureWarning, match="default cookie_name and"):
        FastAPICacheXSessionMiddleware(FastAPI(), session_manager=manager)


def test_middleware_warns_when_the_app_builds_its_stack() -> None:
    """``add_middleware`` defers construction; the first request builds it."""
    manager = SessionManager(MemoryBackend(), SessionConfig(secret_key=SECRET))
    app = FastAPI()
    app.add_middleware(FastAPICacheXSessionMiddleware, session_manager=manager)

    @app.get("/")
    async def index() -> dict[str, bool]:
        return {"ok": True}

    with pytest.warns(FutureWarning, match="__Host-session"):
        response = TestClient(app).get("/")

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("settings", "requirement"),
    [
        ({"cookie_name": "__Host-session"}, "cookie_https_only=True"),
        ({"cookie_name": "__Secure-session"}, "cookie_https_only=True"),
        (
            {
                "cookie_name": "__Host-session",
                "cookie_https_only": True,
                "cookie_path": "/app",
            },
            'cookie_path="/"',
        ),
        (
            {
                "cookie_name": "__Host-session",
                "cookie_https_only": True,
                "cookie_domain": "example.com",
            },
            "cookie_domain=None",
        ),
    ],
)
def test_prefixed_cookie_names_browsers_refuse_warn(
    settings: dict[str, object], requirement: str
) -> None:
    with pytest.warns(UserWarning, match="Version 0.4.0 will reject") as record:
        SessionConfig(secret_key=SECRET, **settings)

    message = str(record[0].message)
    assert requirement in message
    assert "issues/256" in message


@pytest.mark.parametrize(
    "settings",
    [
        {"cookie_name": "__Host-session", "cookie_https_only": True},
        {
            "cookie_name": "__Secure-session",
            "cookie_https_only": True,
            "cookie_path": "/app",
            "cookie_domain": "example.com",
        },
        {"cookie_name": "session", "cookie_domain": "example.com"},
    ],
)
def test_valid_cookie_prefixes_do_not_warn(settings: dict[str, object]) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SessionConfig(secret_key=SECRET, **settings)


# --- #131: get_session_manager through SessionManagerProxy ---------------------


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


@pytest.mark.parametrize("path", ["/manager", "/ip"])
def test_get_session_manager_warns_without_the_proxy(
    manager: SessionManager, config: SessionConfig, path: str
) -> None:
    client = TestClient(_manager_app(manager, config))

    with pytest.warns(FutureWarning, match=r"SessionManagerProxy\.set\(") as record:
        first = client.get(path)
    # Once per app: later requests stay quiet.
    second = client.get(path)

    assert first.status_code == second.status_code == 200
    assert "issues/131" in str(record[0].message)


def test_rotate_session_id_warns_without_the_proxy(
    manager: SessionManager, config: SessionConfig
) -> None:
    client = TestClient(_manager_app(manager, config))

    with pytest.warns(FutureWarning, match="rotate_session_id"):
        response = client.post("/rotate")

    assert response.json() == {"rotated": False}


def test_get_session_manager_warns_when_the_proxy_holds_another_manager(
    manager: SessionManager, config: SessionConfig
) -> None:
    SessionManagerProxy.set(SessionManager(MemoryBackend(), config))
    client = TestClient(_manager_app(manager, config))

    with pytest.warns(
        FutureWarning, match="a different SessionManager is set in SessionManagerProxy"
    ):
        response = client.get("/manager")

    # 0.3.x still answers with the middleware's manager.
    assert response.json() == {"is_same": True}


def test_get_session_manager_warns_when_the_proxy_is_empty(
    manager: SessionManager, config: SessionConfig
) -> None:
    client = TestClient(_manager_app(manager, config))

    with pytest.warns(
        FutureWarning, match="no SessionManager is set in SessionManagerProxy"
    ):
        response = client.get("/manager")

    assert response.json() == {"is_same": True}


def test_get_session_manager_is_silent_when_the_proxy_agrees(
    manager: SessionManager, config: SessionConfig
) -> None:
    SessionManagerProxy.set(manager)
    client = TestClient(_manager_app(manager, config))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        response = client.get("/manager")
        client.post("/rotate")

    assert response.json() == {"is_same": True}


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


# --- #75: token_source_priority names every token source -----------------------

_COOKIE = {"cookie_name": "session", "cookie_https_only": False}


@pytest.mark.parametrize(
    "priority", [["header", "bearer"], ["bearer", "header"], ["header"], []]
)
def test_middleware_warns_for_an_explicit_list_without_cookie(
    priority: list[str],
) -> None:
    config = SessionConfig(secret_key=SECRET, token_source_priority=priority, **_COOKIE)

    with pytest.warns(FutureWarning, match='does not list "cookie"') as record:
        _middleware(config)

    assert "issues/75" in str(record[0].message)


@pytest.mark.parametrize(
    "settings",
    [
        {},  # the default list becomes ["header", "bearer", "cookie"]: same order
        {"token_source_priority": ["header", "bearer", "cookie"]},
        {"token_source_priority": ["bearer", "cookie"]},
        {"token_source_priority": ["cookie"]},
    ],
)
def test_middleware_is_silent_for_the_default_or_a_list_with_cookie(
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


async def test_the_cookie_is_still_read_before_0_4_0_with_or_without_the_entry() -> (
    None
):
    """Listing "cookie" last is today's order, and leaving it out changes nothing yet."""
    backend = MemoryBackend()
    for priority in (["header", "cookie"], ["header"]):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
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


# --- #377: use_bearer_token is removed ------------------------------------------


@pytest.mark.parametrize("value", [True, False])
def test_passing_use_bearer_token_warns(value: bool) -> None:
    with pytest.warns(DeprecationWarning, match="use_bearer_token") as record:
        SessionConfig(secret_key=SECRET, use_bearer_token=value)

    message = str(record[0].message)
    assert "0.4.0" in message
    assert "issues/377" in message


def test_leaving_use_bearer_token_out_is_silent() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SessionConfig(secret_key=SECRET, token_source_priority=["header", "cookie"])
