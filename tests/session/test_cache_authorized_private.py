"""`cache_authorized` answers are private, and a session read sets `Vary` (#372).

`cache_authorized=True` lets a request with credentials use the backend under
a per-caller key, but its response kept the decorator's header: no `private`,
and no `Vary` unless the handler read `request.session`. A CDN in front of the
app keys on the URL alone, so `max-age=60, must-revalidate` (or any header, for
a session token in `X-Session-Token` or a cookie) let it serve one user's
response to the next.
"""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import build_cache_key
from fastapi_cachex import cache
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import AuthenticatedSession
from fastapi_cachex.session.dependencies import OptionalSession
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import FastAPICacheXSessionMiddleware
from fastapi_cachex.session.middleware import SessionMiddleware
from fastapi_cachex.session.models import SessionUser

_AUTH = {"Authorization": "Bearer alice"}


def _per_caller(request: Request) -> str:
    session = getattr(request.state, "__fastapi_cachex_session", None)
    user = session.user.user_id if session and session.user else ""
    return build_cache_key(request, request.headers.get("authorization", ""), user)


def _vary(response: object) -> set[str]:
    header = response.headers.get("vary", "")  # type: ignore[attr-defined]
    return {name.strip().lower() for name in header.split(",") if name.strip()}


@pytest.mark.parametrize(
    ("cache_kwargs", "expected"),
    [
        ({}, "private, max-age=60"),
        ({"must_revalidate": True}, "private, max-age=60, must-revalidate"),
        (
            {"stale": "revalidate", "stale_ttl": 30},
            "private, max-age=60, stale-while-revalidate=30",
        ),
    ],
)
def test_authorization_answers_are_private_on_miss_hit_and_304(
    cache_kwargs: dict[str, object], expected: str
) -> None:
    app = FastAPI()
    calls: list[str] = []

    @app.get("/me")
    @cache(ttl=60, key_builder=_per_caller, cache_authorized=True, **cache_kwargs)  # type: ignore[arg-type]
    async def me(request: Request) -> dict[str, str]:
        calls.append(request.headers["authorization"])
        return {"me": request.headers["authorization"]}

    client = TestClient(app)
    miss = client.get("/me", headers=_AUTH)
    hit = client.get("/me", headers=_AUTH)
    revalidated = client.get(
        "/me", headers={**_AUTH, "If-None-Match": miss.headers["etag"]}
    )

    assert calls == ["Bearer alice"]  # the backend is still used
    assert "age" in hit.headers
    assert revalidated.status_code == 304
    for response in (miss, hit, revalidated):
        assert response.headers["cache-control"] == expected


def test_requests_without_credentials_keep_the_decorator_header() -> None:
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, key_builder=_per_caller, cache_authorized=True)
    async def me() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    client.get("/me")

    assert client.get("/me").headers["cache-control"] == "max-age=60"


def test_public_routes_keep_public() -> None:
    app = FastAPI()

    @app.get("/catalog")
    @cache(ttl=60, public=True)
    async def catalog() -> dict[str, bool]:
        return {"ok": True}

    response = TestClient(app).get("/catalog", headers=_AUTH)

    assert response.headers["cache-control"] == "public, max-age=60"


def _session_app(manager: SessionManager, config: SessionConfig) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        FastAPICacheXSessionMiddleware, session_manager=manager, config=config
    )

    @app.get("/me")
    @cache(ttl=60, key_builder=_per_caller, cache_authorized=True)
    async def me(session: AuthenticatedSession) -> dict[str, str]:
        assert session.user is not None
        return {"user": session.user.user_id}

    @app.get("/greeting")
    async def greeting(session: OptionalSession) -> dict[str, str]:
        return {"hello": session.user.user_id if session and session.user else "guest"}

    @app.get("/plain")
    async def plain() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _token(manager: SessionManager, user_id: str) -> str:
    _session, token = await manager.create_session(user=SessionUser(user_id=user_id))
    return token


async def test_header_session_answer_is_private_and_varies(
    manager: SessionManager, config: SessionConfig
) -> None:
    client = TestClient(_session_app(manager, config))
    token = await _token(manager, "alice")

    for _ in range(2):  # miss, then hit
        response = client.get("/me", headers={config.header_name: token})
        assert response.json() == {"user": "alice"}
        assert response.headers["cache-control"] == "private, max-age=60"
        assert config.header_name.lower() in _vary(response)


async def test_cookie_session_answer_is_private_and_varies_on_cookie(
    manager: SessionManager, config: SessionConfig
) -> None:
    client = TestClient(_session_app(manager, config))
    client.cookies.set(config.cookie_name, await _token(manager, "bob"))

    response = client.get("/me")

    assert response.json() == {"user": "bob"}
    assert response.headers["cache-control"] == "private, max-age=60"
    assert "cookie" in _vary(response)


async def test_a_session_dependency_read_varies_without_a_session(
    manager: SessionManager, config: SessionConfig
) -> None:
    """A guest's answer depends on the token sources as much as a user's does."""
    client = TestClient(_session_app(manager, config))

    guest = client.get("/greeting")
    alice = client.get(
        "/greeting", headers={config.header_name: await _token(manager, "alice")}
    )

    assert guest.json() == {"hello": "guest"}
    assert alice.json() == {"hello": "alice"}
    assert {config.header_name.lower(), "cookie"} <= _vary(guest)
    assert config.header_name.lower() in _vary(alice)


async def test_routes_that_do_not_read_the_session_do_not_vary(
    manager: SessionManager, config: SessionConfig
) -> None:
    client = TestClient(_session_app(manager, config))

    response = client.get(
        "/plain", headers={config.header_name: await _token(manager, "alice")}
    )

    assert "vary" not in response.headers


async def test_deprecated_middleware_varies_on_a_session_read(
    manager: SessionManager, config: SessionConfig
) -> None:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

    @app.get("/greeting")
    async def greeting(session: OptionalSession) -> dict[str, str]:
        return {"hello": session.user.user_id if session and session.user else "guest"}

    @app.get("/plain")
    async def plain() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    token = await _token(manager, "alice")
    with pytest.warns(DeprecationWarning, match="FastAPICacheXSessionMiddleware"):
        response = client.get("/greeting", headers={config.header_name: token})

    assert response.json() == {"hello": "alice"}
    assert config.header_name.lower() in _vary(response)
    assert (
        "vary" not in client.get("/plain", headers={config.header_name: token}).headers
    )
