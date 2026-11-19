"""A response is never shared between callers the key does not tell apart (#574).

Two ways served one caller's response to the next:

- ``cache_authorized=True`` without the caller in the key. The bypass warning
  said to set it "with a key_builder"; setting only the flag shared one entry
  between every caller with ``Authorization`` or session data.
- An API key or cookie read by the route's dependencies (FastAPI's
  ``APIKeyHeader`` / ``APIKeyCookie``, a ``Cookie()`` parameter). Such a
  request carries no ``Authorization`` header, so it was not treated as
  credentialed at all.

Both now bypass the backend and are sent with ``private``.
"""

import logging
from typing import Annotated

import pytest
from fastapi import Cookie
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi.security import APIKeyCookie
from fastapi.security import APIKeyHeader
from fastapi.security import APIKeyQuery
from fastapi.testclient import TestClient

from fastapi_cachex import cache
from fastapi_cachex import default_key_builder

_LOGGER = "fastapi_cachex.cache"
_API_KEY = APIKeyHeader(name="X-API-Key")


def _get(client: TestClient, path: str, **kwargs: object) -> tuple[object, str]:
    response = client.get(path, **kwargs)  # type: ignore[arg-type]
    return response.json(), response.headers["X-Cache"]


def _authorized_app(**cache_kwargs: object) -> TestClient:
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, debug_header=True, cache_authorized=True, **cache_kwargs)  # type: ignore[arg-type]
    async def me(request: Request) -> str:
        return request.headers["authorization"]

    return TestClient(app)


async def test_cache_authorized_without_the_caller_in_the_key_bypasses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _authorized_app()

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        alice = client.get("/me", headers={"Authorization": "alice"})
        bob = client.get("/me", headers={"Authorization": "bob"})

    assert bob.json() == "bob"
    assert bob.headers["X-Cache"] == "BYPASS"
    assert bob.headers["X-Cache-Reason"] == "authorization"
    assert alice.headers["Cache-Control"].startswith("private")
    warnings = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
    assert len(warnings) == 1
    assert "does not identify the caller" in warnings[0]
    assert "alice" not in warnings[0]


async def test_cache_authorized_with_the_header_in_vary_caches_per_caller() -> None:
    client = _authorized_app(vary=["Authorization"])

    assert _get(client, "/me", headers={"Authorization": "alice"}) == ("alice", "MISS")
    assert _get(client, "/me", headers={"Authorization": "bob"}) == ("bob", "MISS")
    assert _get(client, "/me", headers={"Authorization": "alice"}) == ("alice", "HIT")


async def test_sharing_between_callers_can_be_asked_for() -> None:
    client = _authorized_app(key_builder=default_key_builder)

    assert _get(client, "/me", headers={"Authorization": "alice"}) == ("alice", "MISS")
    assert _get(client, "/me", headers={"Authorization": "bob"}) == ("alice", "HIT")


def _api_key_app(**cache_kwargs: object) -> TestClient:
    app = FastAPI()

    async def current_user(key: str = Depends(_API_KEY)) -> str:
        return f"user-{key}"

    @app.get("/me")
    @cache(ttl=60, debug_header=True, **cache_kwargs)  # type: ignore[arg-type]
    async def me(user: Annotated[str, Depends(current_user)]) -> str:
        return user

    return TestClient(app)


async def test_an_api_key_header_in_a_nested_dependency_bypasses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _api_key_app()

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        alice = client.get("/me", headers={"X-API-Key": "alice"})
        bob = client.get("/me", headers={"X-API-Key": "bob"})

    assert (alice.json(), bob.json()) == ("user-alice", "user-bob")
    assert bob.headers["X-Cache"] == "BYPASS"
    assert bob.headers["X-Cache-Reason"] == "credential"
    assert bob.headers["Cache-Control"].startswith("private")
    warnings = [r.getMessage() for r in caplog.records if r.name == _LOGGER]
    assert len(warnings) == 1
    assert "X-API-Key header" in warnings[0]
    assert "alice" not in warnings[0]


async def test_an_api_key_in_vary_with_cache_authorized_caches_per_key() -> None:
    client = _api_key_app(vary=["X-API-Key"], cache_authorized=True)

    alice = {"headers": {"X-API-Key": "alice"}}
    assert _get(client, "/me", **alice) == ("user-alice", "MISS")
    assert _get(client, "/me", headers={"X-API-Key": "bob"}) == ("user-bob", "MISS")
    assert _get(client, "/me", **alice) == ("user-alice", "HIT")


async def test_public_shares_an_api_key_route() -> None:
    client = _api_key_app(public=True)

    assert _get(client, "/me", headers={"X-API-Key": "a"}) == ("user-a", "MISS")
    assert _get(client, "/me", headers={"X-API-Key": "b"}) == ("user-a", "HIT")


@pytest.mark.parametrize("kind", ["APIKeyCookie", "Cookie"])
async def test_an_identifying_cookie_bypasses_only_when_sent(kind: str) -> None:
    app = FastAPI()
    if kind == "APIKeyCookie":
        dependency = Depends(APIKeyCookie(name="token", auto_error=False))
    else:
        dependency = Cookie(None)

    @app.get("/me")
    @cache(ttl=60, debug_header=True)
    async def me(token: str | None = dependency) -> str | None:
        return token

    client = TestClient(app)
    assert _get(client, "/me") == (None, "MISS")
    assert _get(client, "/me") == (None, "HIT")
    client.cookies.set("token", "alice")
    assert _get(client, "/me") == ("alice", "BYPASS")
    client.cookies.set("token", "bob")
    assert _get(client, "/me") == ("bob", "BYPASS")


async def test_an_unrelated_cookie_or_a_query_api_key_is_not_a_credential() -> None:
    app = FastAPI()

    @app.get("/items")
    @cache(ttl=60, debug_header=True)
    async def items(key: Annotated[str, Depends(APIKeyQuery(name="key"))]) -> str:
        # The query string is part of the key.
        return key

    client = TestClient(app, cookies={"theme": "dark"})
    assert _get(client, "/items?key=a") == ("a", "MISS")
    assert _get(client, "/items?key=b") == ("b", "MISS")
    assert _get(client, "/items?key=a") == ("a", "HIT")
