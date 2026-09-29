"""A route without a positive ttl still marks a credentialed response private (#362).

Such routes skip the backend anyway, so the credential check was skipped with
it and the response kept the decorator's header: `@cache(ttl=0,
must_revalidate=True)` answered an `Authorization` request with `max-age=0,
must-revalidate`, which RFC 9111 §3.5 lets a shared cache store.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import (
    SessionMiddleware as StarletteSessionMiddleware,
)

from fastapi_cachex import cache
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.proxy import BackendProxy

_AUTH = {"Authorization": "Bearer alice"}


def _client(**cache_kwargs: object) -> TestClient:
    app = FastAPI()

    @app.get("/me")
    @cache(**cache_kwargs)  # type: ignore[arg-type]
    async def me() -> dict[str, str]:
        return {"me": "x"}

    return TestClient(app)


@pytest.mark.parametrize(
    ("cache_kwargs", "expected"),
    [
        ({"ttl": 0, "must_revalidate": True}, "private, max-age=0, must-revalidate"),
        ({"ttl": 0}, "private, max-age=0"),
        ({"must_revalidate": True}, "private, must-revalidate"),
        ({}, "private"),
        ({"ttl": 0, "no_cache": True}, "private, no-cache"),
    ],
)
def test_authorized_request_gets_private(
    cache_kwargs: dict[str, object], expected: str
) -> None:
    response = _client(**cache_kwargs).get("/me", headers=_AUTH)

    assert response.status_code == 200
    assert response.headers["cache-control"] == expected


def test_not_modified_is_private_too() -> None:
    client = _client(ttl=0, must_revalidate=True)
    etag = client.get("/me", headers=_AUTH).headers["etag"]

    response = client.get("/me", headers={**_AUTH, "If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["cache-control"] == "private, max-age=0, must-revalidate"


def test_request_without_credentials_is_unchanged() -> None:
    response = _client(ttl=0, must_revalidate=True).get("/me")

    assert response.headers["cache-control"] == "max-age=0, must-revalidate"


@pytest.mark.parametrize(
    ("cache_kwargs", "expected"),
    [
        ({"ttl": 0, "public": True}, "public, max-age=0"),
        ({"ttl": 0, "cache_authorized": True}, "private, max-age=0"),
        ({"ttl": 0, "private": True}, "private, max-age=0"),
    ],
)
def test_public_opted_in_and_private_routes(
    cache_kwargs: dict[str, object], expected: str
) -> None:
    response = _client(**cache_kwargs).get("/me", headers=_AUTH)

    assert response.headers["cache-control"] == expected


def test_session_request_gets_private() -> None:
    app = FastAPI()
    app.add_middleware(StarletteSessionMiddleware, secret_key="x" * 32)

    @app.get("/cart")
    @cache(ttl=0, must_revalidate=True)
    async def cart(request: Request) -> dict[str, list[str]]:
        return {"cart": request.session.get("cart", [])}

    @app.get("/add")
    async def add(request: Request) -> dict[str, bool]:
        request.session["cart"] = ["apple"]
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/cart").headers["cache-control"] == "max-age=0, must-revalidate"
    client.get("/add")

    response = client.get("/cart")

    assert response.json() == {"cart": ["apple"]}
    assert response.headers["cache-control"] == "private, max-age=0, must-revalidate"


async def test_backend_stays_untouched_and_nothing_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The route never had cache hits to lose, so the bypass warning stays quiet."""
    backend = BackendProxy.get()
    assert isinstance(backend, MemoryBackend)
    client = _client(ttl=0, must_revalidate=True)

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.cache"):
        client.get("/me", headers=_AUTH)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert await backend.get_all_keys() == []
