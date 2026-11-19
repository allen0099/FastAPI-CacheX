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
from fastapi import APIRouter
from fastapi import Cookie
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi.security import APIKeyCookie
from fastapi.security import APIKeyHeader
from fastapi.security import APIKeyQuery
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pydantic import Field
from starlette.middleware.sessions import SessionMiddleware

from fastapi_cachex import BackendProxy
from fastapi_cachex import cache
from fastapi_cachex import default_key_builder

_LOGGER = "fastapi_cachex.cache"
_API_KEY = APIKeyHeader(name="X-API-Key")
_OPTIONAL_KEY = APIKeyHeader(name="X-API-Key", auto_error=False)


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


async def test_every_credential_must_be_keyed_not_just_the_first() -> None:
    """A gate token in Authorization shared by everyone keyed nothing else."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="s" * 32)

    @app.get("/login/{user}")
    async def login(user: str, request: Request) -> None:
        request.session["user"] = user

    @app.get("/me")
    @cache(ttl=60, debug_header=True, cache_authorized=True, vary=["Authorization"])
    async def me(
        request: Request, key: Annotated[str | None, Depends(_OPTIONAL_KEY)]
    ) -> str:
        return f"{request.session.get('user')}:{key}"

    gate = {"Authorization": "Basic gate"}
    alice, bob = TestClient(app, headers=gate), TestClient(app, headers=gate)
    alice.get("/login/alice")
    bob.get("/login/bob")
    assert _get(alice, "/me") == ("alice:None", "BYPASS")
    assert _get(bob, "/me") == ("bob:None", "BYPASS")
    anonymous = TestClient(app, headers=gate)
    assert _get(anonymous, "/me", headers={"X-API-Key": "a"}) == ("None:a", "BYPASS")
    assert _get(anonymous, "/me", headers={"X-API-Key": "b"}) == ("None:b", "BYPASS")
    # Only the shared token: keyed by vary, so it is cached.
    assert _get(anonymous, "/me") == ("None:None", "MISS")
    assert _get(anonymous, "/me") == ("None:None", "HIT")


async def test_a_vary_normaliser_does_not_identify_the_caller() -> None:
    """`vary={"Cookie": pick_lang}` maps every caller with one language to one entry."""
    app = FastAPI()

    @app.get("/me")
    @cache(
        ttl=60,
        debug_header=True,
        cache_authorized=True,
        vary={"Cookie": lambda value: "en"},
    )
    async def me(tok: Annotated[str | None, Cookie()] = None) -> str | None:
        return tok

    client = TestClient(app)
    client.cookies.set("tok", "alice")
    assert _get(client, "/me") == ("alice", "BYPASS")
    client.cookies.set("tok", "bob")
    assert _get(client, "/me") == ("bob", "BYPASS")


class _Cookies(BaseModel):
    tok: str | None = None


async def test_a_cookie_parameter_model_counts_its_fields() -> None:
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, debug_header=True)
    async def me(cookies: Annotated[_Cookies, Cookie()]) -> str | None:
        return cookies.tok

    client = TestClient(app)
    client.cookies.set("tok", "alice")
    assert _get(client, "/me") == ("alice", "BYPASS")
    client.cookies.set("tok", "bob")
    assert _get(client, "/me") == ("bob", "BYPASS")


async def test_an_aliased_cookie_and_router_and_app_dependencies_count() -> None:
    inner = FastAPI(dependencies=[Depends(_API_KEY)])
    router = APIRouter(
        dependencies=[Depends(APIKeyCookie(name="sid", auto_error=False))]
    )

    @cache(ttl=60, debug_header=True)
    async def items(
        request: Request, name: Annotated[str | None, Cookie(alias="who")] = None
    ) -> str:
        return f"{request.headers.get('x-api-key')}:{request.cookies}"

    router.add_api_route("/items", items, methods=["GET", "HEAD"])
    inner.include_router(router)
    # The same function on another app without those dependencies.
    outer = FastAPI()
    outer.add_api_route("/items", items, methods=["GET"])
    outer.mount("/v2", inner)

    client = TestClient(outer)
    # Memoised for the outer route first; the mounted one still walks its own.
    assert _get(client, "/items") == ("None:{}", "MISS")
    assert _get(client, "/v2/items", headers={"X-API-Key": "a"})[1] == "BYPASS"
    assert _get(client, "/v2/items", headers={"X-API-Key": "b"})[0] == "b:{}"
    response = client.head("/v2/items", headers={"X-API-Key": "a"})
    assert response.headers["X-Cache"] == "BYPASS"
    assert response.headers["X-Cache-Reason"] == "credential"


async def test_an_api_key_listed_in_vary_is_hashed_in_the_key() -> None:
    """Following the advice to list the header must not put the key in clear."""
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, vary=["X-API-Key"], cache_authorized=True)
    async def me(key: Annotated[str, Depends(_API_KEY)]) -> str:
        return key

    client = TestClient(app)
    client.get("/me", headers={"X-API-Key": "SECRET123"})
    backend = BackendProxy.get()
    keys = await backend.get_all_keys()
    assert keys
    assert not any("SECRET123" in key for key in keys)
    assert any("x-api-key=sha256:" in key for key in keys)


class _AliasedCookies(BaseModel):
    model_config = {"populate_by_name": True}

    session_id: str | None = Field(default=None, alias="sessionId")
    sid: str | None = Field(default=None, validation_alias="sess")


@pytest.mark.parametrize("cookie", ["sessionId", "session_id", "sess", "sx"])
async def test_every_name_a_cookie_is_read_by_counts(cookie: str) -> None:
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, debug_header=True)
    async def me(
        cookies: Annotated[_AliasedCookies, Cookie()],
        s: Annotated[str | None, Cookie(validation_alias="sx")] = None,
    ) -> str | None:
        return cookies.session_id or cookies.sid or s

    client = TestClient(app)
    client.cookies.set(cookie, "x")
    if client.get("/me").status_code == 422:
        # Older FastAPI and pydantic refuse these models: nothing to leak.
        pytest.skip("this FastAPI does not read the cookie by that name")
    assert _get(client, "/me") == ("x", "BYPASS")
    client.cookies.set(cookie, "y")
    assert _get(client, "/me") == ("y", "BYPASS")


async def test_an_api_key_header_vary_does_not_hash_does_not_count() -> None:
    """`vary=["X-Client"]` would put the raw API key into the cache key."""
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, debug_header=True, vary=["X-Client"], cache_authorized=True)
    async def me(key: Annotated[str, Depends(APIKeyHeader(name="X-Client"))]) -> str:
        return key

    client = TestClient(app)
    assert _get(client, "/me", headers={"X-Client": "a"}) == ("a", "BYPASS")
    assert not await BackendProxy.get().get_all_keys()
