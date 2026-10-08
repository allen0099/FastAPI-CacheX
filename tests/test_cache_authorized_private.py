"""`cache_authorized` answers are private (#372).

`cache_authorized=True` lets a request with credentials use the backend under
a per-caller key, but its response kept the decorator's header: no `private`,
so a CDN in front of the app, which keys on the URL alone, could serve one
user's response to the next under `max-age=60, must-revalidate`.
"""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import build_cache_key
from fastapi_cachex import cache

_AUTH = {"Authorization": "Bearer alice"}


def _per_caller(request: Request) -> str:
    return build_cache_key(request, request.headers.get("authorization", ""))


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
