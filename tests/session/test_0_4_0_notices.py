"""Advance notices for the session changes 0.4.0 makes (#352).

- #256: the default session cookie becomes ``__Host-session`` with Secure.
- #131: ``get_session_manager`` resolves through ``SessionManagerProxy`` only.
"""

import warnings

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import FastAPICacheXSessionMiddleware
from fastapi_cachex.session import SessionConfig
from fastapi_cachex.session import SessionManager
from fastapi_cachex.session.dependencies import ClientIPDep
from fastapi_cachex.session.dependencies import SessionManagerDep
from fastapi_cachex.session.dependencies import rotate_session_id
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

    assert requirement in str(record[0].message)


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

    with pytest.warns(FutureWarning, match="not the one set in SessionManagerProxy"):
        response = client.get("/manager")

    # 0.3.x still answers with the middleware's manager.
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
