"""Responses that carry a session token must never be stored by a cache (#297).

A token is a credential: a CDN or reverse proxy that stored a response with
``Set-Cookie`` or the token header would hand it to the next visitor. Every
response the session middleware writes a token or a clearing cookie to gets
``Cache-Control: private, no-store`` and ``Vary`` on the transport headers,
whether or not the handler touched ``request.session``.
"""

from typing import Any

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex import cache
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import get_session
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.models import SessionUser

_NO_STORE = "private, no-store"
_PUBLIC = "public, max-age=60"


@pytest.fixture
def sliding_config() -> SessionConfig:
    """A config that renews the token on every load."""
    return SessionConfig(
        secret_key="a" * 32,
        sliding_threshold=1.0,
        cookie_name="session",
        cookie_https_only=False,
    )


@pytest.fixture
def sliding_manager(
    backend: MemoryBackend, sliding_config: SessionConfig
) -> SessionManager:
    return SessionManager(backend, sliding_config)


def _vary(response: Any) -> list[str]:
    """The response's Vary header as lowercased names, duplicates kept."""
    return [
        name.strip().lower()
        for name in response.headers.get("vary", "").split(",")
        if name.strip()
    ]


def _app(
    manager: SessionManager,
    config: SessionConfig,
) -> FastAPI:
    """An app with a publicly cacheable route and routes that touch the session."""
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/public")
    @cache(ttl=60, public=True)
    async def public_route() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/vary-accept")
    async def vary_accept_route(response: Response) -> dict[str, bool]:
        response.headers["Vary"] = "Accept-Encoding, Cookie"
        response.headers["Cache-Control"] = _PUBLIC
        return {"ok": True}

    @app.get("/vary-star")
    async def vary_star_route(response: Response) -> dict[str, bool]:
        response.headers["Vary"] = "*"
        return {"ok": True}

    @app.get("/write")
    async def write_route(request: Request) -> dict[str, bool]:
        request.session["cart"] = [1]
        return {"ok": True}

    @app.get("/logout")
    async def logout_route(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"ok": True}

    @app.get("/pop")
    async def pop_route(request: Request) -> dict[str, Any]:
        return {"flash": request.session.pop("flash", None)}

    @app.post("/login")
    async def login_route(session=Depends(get_session)) -> dict[str, bool]:
        await manager.regenerate_session_id(session)
        return {"ok": True}

    return app


def _assert_not_storable(response: Any, vary: set[str]) -> None:
    assert response.headers["cache-control"] == _NO_STORE
    assert set(_vary(response)) == vary


def test_a_new_session_cookie_is_not_storable(
    manager: SessionManager, config: SessionConfig
) -> None:
    client = TestClient(_app(manager, config))

    response = client.get("/write")

    assert config.cookie_name in response.headers["set-cookie"]
    _assert_not_storable(
        response, {config.header_name.lower(), "authorization", "cookie"}
    )


async def test_a_sliding_renewal_cookie_overrides_a_public_route(
    sliding_manager: SessionManager, sliding_config: SessionConfig
) -> None:
    """The repro from #297: the route never touches the session."""
    _session, token = await sliding_manager.create_session(
        user=SessionUser(user_id="u")
    )
    client = TestClient(_app(sliding_manager, sliding_config))
    client.cookies.set(sliding_config.cookie_name, token)

    response = client.get("/public")

    assert sliding_config.cookie_name in response.headers["set-cookie"]
    _assert_not_storable(
        response, {sliding_config.header_name.lower(), "authorization", "cookie"}
    )


async def test_a_sliding_renewal_header_overrides_a_public_route(
    sliding_manager: SessionManager, sliding_config: SessionConfig
) -> None:
    _session, token = await sliding_manager.create_session(
        user=SessionUser(user_id="u")
    )
    client = TestClient(_app(sliding_manager, sliding_config))

    response = client.get("/public", headers={sliding_config.header_name: token})

    assert sliding_config.header_name in response.headers
    assert "set-cookie" not in response.headers
    _assert_not_storable(response, {sliding_config.header_name.lower()})


async def test_a_sliding_renewal_over_bearer_varies_on_authorization(
    sliding_manager: SessionManager, sliding_config: SessionConfig
) -> None:
    _session, token = await sliding_manager.create_session(
        user=SessionUser(user_id="u")
    )
    client = TestClient(_app(sliding_manager, sliding_config))

    response = client.get("/public", headers={"Authorization": f"Bearer {token}"})

    assert sliding_config.header_name in response.headers
    _assert_not_storable(
        response, {sliding_config.header_name.lower(), "authorization"}
    )


async def test_existing_vary_values_are_kept_and_not_duplicated(
    sliding_manager: SessionManager, sliding_config: SessionConfig
) -> None:
    _session, token = await sliding_manager.create_session(
        user=SessionUser(user_id="u")
    )
    client = TestClient(_app(sliding_manager, sliding_config))
    client.cookies.set(sliding_config.cookie_name, token)

    response = client.get("/vary-accept")

    assert response.headers["cache-control"] == _NO_STORE
    vary = _vary(response)
    assert sorted(vary) == sorted(
        {
            "accept-encoding",
            "cookie",
            sliding_config.header_name.lower(),
            "authorization",
        }
    )


async def test_vary_star_is_left_as_it_is(
    sliding_manager: SessionManager, sliding_config: SessionConfig
) -> None:
    """``Vary: *`` already covers every header; adding names to it is noise."""
    _session, token = await sliding_manager.create_session(
        user=SessionUser(user_id="u")
    )
    client = TestClient(_app(sliding_manager, sliding_config))
    client.cookies.set(sliding_config.cookie_name, token)

    response = client.get("/vary-star")

    assert response.headers["cache-control"] == _NO_STORE
    assert response.headers["vary"] == "*"


@pytest.mark.parametrize("transport", ["cookie", "header"])
async def test_a_regenerated_session_id_is_not_storable(
    manager: SessionManager, config: SessionConfig, transport: str
) -> None:
    _session, token = await manager.create_session(user=SessionUser(user_id="u"))
    client = TestClient(_app(manager, config))

    if transport == "cookie":
        client.cookies.set(config.cookie_name, token)
        response = client.post("/login")
        assert config.cookie_name in response.headers["set-cookie"]
        vary = {config.header_name.lower(), "authorization", "cookie"}
    else:
        response = client.post("/login", headers={config.header_name: token})
        assert response.headers[config.header_name] != token
        vary = {config.header_name.lower()}

    _assert_not_storable(response, vary)


async def test_a_logout_clearing_cookie_is_not_storable(
    manager: SessionManager, config: SessionConfig
) -> None:
    _session, token = await manager.create_session(user=SessionUser(user_id="u"))
    client = TestClient(_app(manager, config))
    client.cookies.set(config.cookie_name, token)

    response = client.get("/logout")

    assert "1970" in response.headers["set-cookie"]
    _assert_not_storable(
        response, {config.header_name.lower(), "authorization", "cookie"}
    )


async def test_an_emptied_anonymous_session_clearing_cookie_is_not_storable(
    manager: SessionManager, config: SessionConfig
) -> None:
    _session, token = await manager.create_anonymous_session(flash="hi")
    client = TestClient(_app(manager, config))
    client.cookies.set(config.cookie_name, token)

    response = client.get("/pop")

    assert "1970" in response.headers["set-cookie"]
    _assert_not_storable(
        response, {config.header_name.lower(), "authorization", "cookie"}
    )


async def test_an_emptied_anonymous_header_session_sends_nothing_to_forbid(
    manager: SessionManager, config: SessionConfig
) -> None:
    """A header client just drops its dangling token, so no header is sent."""
    _session, token = await manager.create_anonymous_session(flash="hi")
    client = TestClient(_app(manager, config))

    response = client.get("/pop", headers={config.header_name: token})

    assert response.json() == {"flash": "hi"}
    assert config.header_name not in response.headers
    assert "set-cookie" not in response.headers
    assert "cache-control" not in response.headers


async def test_a_response_without_a_token_keeps_its_cache_control(
    manager: SessionManager, config: SessionConfig
) -> None:
    """A loaded session outside the renewal window sends no token."""
    _session, token = await manager.create_session(user=SessionUser(user_id="u"))
    client = TestClient(_app(manager, config))
    client.cookies.set(config.cookie_name, token)

    response = client.get("/public")

    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == _PUBLIC
    assert "vary" not in response.headers


def test_a_response_without_a_session_keeps_its_cache_control(
    manager: SessionManager, config: SessionConfig
) -> None:
    client = TestClient(_app(manager, config))

    response = client.get("/public")

    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == _PUBLIC
    assert "vary" not in response.headers
