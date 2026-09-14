"""Tests for `@cache(private=True)` keeping responses out of the shared backend.

`private` used to affect only the `Cache-Control` header: the response was
still stored in — and served from — the backend every worker shares, and the
default cache key carries no user identity, so one caller's response was handed
to the next.
"""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex.cache import cache
from fastapi_cachex.proxy import BackendProxy


@pytest.mark.asyncio
async def test_private_responses_are_not_shared_between_users():
    """Two callers on the same route must each see their own body."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/me")
    @cache(ttl=60, private=True)
    async def me(request: Request):
        who = request.headers.get("authorization", "anonymous")
        return Response(content=f"profile of {who}", media_type="text/plain")

    alice = client.get("/me", headers={"Authorization": "alice"})
    bob = client.get("/me", headers={"Authorization": "bob"})

    assert alice.text == "profile of alice"
    assert bob.text == "profile of bob"

    # Nothing about this route reached the shared backend.
    assert await BackendProxy.get().get_all_keys() == []


def test_private_response_still_advertises_private_caching():
    """The Cache-Control header is the whole point of `private`; keep it."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/private-headers")
    @cache(ttl=30, private=True)
    async def private_headers():
        return Response(content="x", media_type="text/plain")

    cache_control = client.get("/private-headers").headers["Cache-Control"]

    assert "private" in cache_control
    assert "max-age=30" in cache_control


def test_private_revalidates_with_if_none_match():
    """Unchanged private content still answers 304 against a fresh render."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/private-etag")
    @cache(ttl=30, private=True)
    async def private_etag():
        return Response(content="stable", media_type="text/plain")

    etag = client.get("/private-etag").headers["ETag"]
    revalidated = client.get("/private-etag", headers={"If-None-Match": etag})

    assert revalidated.status_code == 304
    assert revalidated.headers["ETag"] == etag
    assert "private" in revalidated.headers["Cache-Control"]


def test_private_serves_changed_content_with_a_new_etag():
    """A changed private body must not be answered with a stale 304."""
    app = FastAPI()
    client = TestClient(app)
    body = {"value": "first"}

    @app.get("/private-changing")
    @cache(ttl=30, private=True)
    async def private_changing():
        return Response(content=body["value"], media_type="text/plain")

    etag = client.get("/private-changing").headers["ETag"]
    body["value"] = "second"

    response = client.get("/private-changing", headers={"If-None-Match": etag})

    assert response.status_code == 200
    assert response.text == "second"
    assert response.headers["ETag"] != etag


def test_private_runs_the_handler_on_every_request():
    """Without a shared copy there is nothing to serve a hit from."""
    app = FastAPI()
    client = TestClient(app)
    calls = {"n": 0}

    @app.get("/private-calls")
    @cache(ttl=60, private=True)
    async def private_calls():
        calls["n"] += 1
        return Response(content="x", media_type="text/plain")

    client.get("/private-calls")
    client.get("/private-calls")

    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_private_error_response_passes_through():
    """An uncacheable status is served as-is and stored nowhere."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/private-error")
    @cache(ttl=60, private=True)
    async def private_error():
        return Response(content="nope", status_code=404)

    response = client.get("/private-error")

    assert response.status_code == 404
    assert await BackendProxy.get().get_all_keys() == []


def test_no_store_still_wins_over_private():
    """`no_store` is checked first and keeps its header."""
    app = FastAPI()
    client = TestClient(app)

    @app.get("/private-no-store")
    @cache(private=True, no_store=True)
    async def private_no_store():
        return Response(content="x", media_type="text/plain")

    assert client.get("/private-no-store").headers["Cache-Control"] == "no-store"
