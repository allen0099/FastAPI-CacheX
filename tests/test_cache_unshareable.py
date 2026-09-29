"""Tests for `@cache` refusing to share responses that belong to one caller (#296).

A response was written to the shared backend, and replayed to everyone, even
when the handler marked it ``private``/``no-store``, when it set a cookie, or
when the request carried ``Authorization``; the handler's own
``Cache-Control`` was then overwritten with the decorator's.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex.cache import cache
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CACHE_KEY_SEPARATOR


def _key(path: str) -> str:
    """The key `default_key_builder` produces for a TestClient GET."""
    return f"GET|||testserver|||{path}|||"


async def test_issue_repro_private_no_store_with_authorization():
    """The report in #296: bob must not get alice's response."""
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60)
    async def me(request: Request, response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        return {"user": request.headers.get("authorization")}

    client = TestClient(app)
    alice = client.get("/me", headers={"Authorization": "Bearer alice"})
    bob = client.get("/me", headers={"Authorization": "Bearer bob"})

    assert alice.json() == {"user": "Bearer alice"}
    assert bob.json() == {"user": "Bearer bob"}
    assert bob.headers["Cache-Control"] == "private, no-store"
    assert await BackendProxy.get().get_all_keys() == []


@pytest.mark.parametrize(
    "handler_cache_control",
    ["private", "no-store", "Private, max-age=5", "max-age=5 , NO-STORE"],
)
async def test_response_marked_private_or_no_store_is_not_stored(
    handler_cache_control: str,
):
    """No `Authorization` involved: the handler's own marking is enough."""
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/marked")
    @cache(ttl=60)
    async def marked(response: Response):
        calls["n"] += 1
        response.headers["Cache-Control"] = handler_cache_control
        return {"n": calls["n"]}

    client = TestClient(app)
    first = client.get("/marked")
    second = client.get("/marked")

    assert first.json() == {"n": 1}
    assert second.json() == {"n": 2}
    # The handler's header is sent as-is, not replaced by `max-age=60`.
    assert second.headers["Cache-Control"] == handler_cache_control
    assert await BackendProxy.get().get(_key("/marked")) is None


@pytest.mark.parametrize(
    "handler_cache_control", ["max-age=5", "no-cache", "x-private-hint, public"]
)
async def test_other_directives_are_still_stored(handler_cache_control: str):
    """Only whole `private`/`no-store` tokens stop a write."""
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/shareable")
    @cache(ttl=60)
    async def shareable(response: Response):
        calls["n"] += 1
        response.headers["Cache-Control"] = handler_cache_control
        return {"n": calls["n"]}

    client = TestClient(app)
    client.get("/shareable")
    hit = client.get("/shareable")

    assert hit.json() == {"n": 1}
    assert hit.headers["Cache-Control"] == "max-age=60"
    assert await BackendProxy.get().get(_key("/shareable")) is not None


async def test_response_setting_a_cookie_is_not_stored():
    """The body that came with a cookie is not replayed to other callers."""
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/visit")
    @cache(ttl=60)
    async def visit(response: Response):
        calls["n"] += 1
        response.set_cookie("visitor", f"v{calls['n']}")
        return {"visitor": f"v{calls['n']}"}

    client = TestClient(app)
    first = client.get("/visit")
    second = client.get("/visit")

    assert first.json() == {"visitor": "v1"}
    assert second.json() == {"visitor": "v2"}
    assert second.cookies["visitor"] == "v2"
    assert second.headers["Cache-Control"] == "private, max-age=60"
    assert await BackendProxy.get().get(_key("/visit")) is None


async def test_authorization_request_bypasses_the_backend():
    """Neither read nor written: not even an anonymous entry is served."""
    app = FastAPI()

    @app.get("/greeting")
    @cache(ttl=60)
    async def greeting(request: Request):
        return {"for": request.headers.get("authorization", "anonymous")}

    client = TestClient(app)
    assert client.get("/greeting").json() == {"for": "anonymous"}
    stored = await BackendProxy.get().get(_key("/greeting"))
    assert stored is not None

    alice = client.get("/greeting", headers={"Authorization": "Bearer alice"})
    bob = client.get("/greeting", headers={"Authorization": "Bearer bob"})

    assert alice.json() == {"for": "Bearer alice"}
    assert bob.json() == {"for": "Bearer bob"}
    assert bob.headers["Cache-Control"] == "private, max-age=60"
    # The anonymous entry is untouched.
    assert await BackendProxy.get().get(_key("/greeting")) == stored


def test_authorization_request_still_revalidates():
    """ETag revalidation runs against the fresh render, as for `private`."""
    app = FastAPI()

    @app.get("/doc")
    @cache(ttl=60)
    async def doc():
        return {"doc": 1}

    client = TestClient(app)
    auth = {"Authorization": "Bearer alice"}
    etag = client.get("/doc", headers=auth).headers["ETag"]

    revalidated = client.get("/doc", headers={**auth, "If-None-Match": etag})

    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == "private, max-age=60"


def test_bypassed_304_keeps_the_handler_cache_control():
    """A 304 for a response the handler marked private repeats its header."""
    app = FastAPI()

    @app.get("/mine")
    @cache(ttl=60)
    async def mine(response: Response):
        response.headers["Cache-Control"] = "private"
        return {"mine": True}

    client = TestClient(app)
    auth = {"Authorization": "Bearer alice"}
    etag = client.get("/mine", headers=auth).headers["ETag"]

    revalidated = client.get("/mine", headers={**auth, "If-None-Match": etag})

    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == "private"


async def test_public_route_caches_authorization_requests():
    """RFC 9111 §3.5 lets `public` responses be reused for any caller."""
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/catalog")
    @cache(ttl=60, public=True)
    async def catalog():
        calls["n"] += 1
        return {"n": calls["n"]}

    client = TestClient(app)
    client.get("/catalog", headers={"Authorization": "Bearer alice"})
    bob = client.get("/catalog", headers={"Authorization": "Bearer bob"})

    assert bob.json() == {"n": 1}
    assert calls["n"] == 1
    assert await BackendProxy.get().get(_key("/catalog")) is not None


async def test_cache_authorized_caches_per_user_entries():
    """The opt-in is for key builders that carry the caller's identity."""
    app = FastAPI()
    calls = {"n": 0}

    def per_user_key(request: Request) -> str:
        user = request.headers.get("authorization", "anonymous")
        return f"{request.url.path}{CACHE_KEY_SEPARATOR}{user}"

    @app.get("/dashboard")
    @cache(ttl=60, key_builder=per_user_key, cache_authorized=True)
    async def dashboard(request: Request):
        calls["n"] += 1
        return {"for": request.headers["authorization"], "n": calls["n"]}

    client = TestClient(app)
    alice = {"Authorization": "Bearer alice"}
    client.get("/dashboard", headers=alice)
    alice_hit = client.get("/dashboard", headers=alice)
    bob = client.get("/dashboard", headers={"Authorization": "Bearer bob"})

    assert alice_hit.json() == {"for": "Bearer alice", "n": 1}
    assert bob.json() == {"for": "Bearer bob", "n": 2}
    assert await BackendProxy.get().get("/dashboard|||Bearer alice") is not None


async def test_unshareable_render_leaves_an_existing_entry_alone():
    """With `no_cache` the handler runs on every request; a private render must
    neither overwrite nor evict what an earlier shareable one stored."""
    app = FastAPI()
    state = {"private": False}

    @app.get("/feed")
    @cache(ttl=60, no_cache=True)
    async def feed(response: Response):
        if state["private"]:
            response.headers["Cache-Control"] = "private"
            return {"feed": "personal"}
        return {"feed": "shared"}

    client = TestClient(app)
    client.get("/feed")
    stored = await BackendProxy.get().get(_key("/feed"))
    assert stored is not None

    state["private"] = True
    response = client.get("/feed")

    assert response.json() == {"feed": "personal"}
    assert response.headers["Cache-Control"] == "private"
    assert await BackendProxy.get().get(_key("/feed")) == stored


def test_no_cache_304_keeps_the_handler_cache_control():
    """The `no_cache` revalidation path repeats the handler's header too."""
    app = FastAPI()

    @app.get("/inbox")
    @cache(ttl=60, no_cache=True)
    async def inbox(response: Response):
        response.headers["Cache-Control"] = "private, no-cache"
        return {"inbox": []}

    client = TestClient(app)
    etag = client.get("/inbox").headers["ETag"]

    revalidated = client.get("/inbox", headers={"If-None-Match": etag})

    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == "private, no-cache"


def test_no_store_decorator_overrides_the_handler_header():
    """`no_store=True` stays the strictest answer, whatever the handler sent."""
    app = FastAPI()

    @app.get("/secret")
    @cache(no_store=True)
    async def secret(response: Response):
        response.headers["Cache-Control"] = "private, max-age=3600"
        return {"secret": True}

    response = TestClient(app).get("/secret")

    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("headers", "cache_control", "set_cookie", "expected"),
    [
        ({"Authorization": "Bearer a"}, None, False, "Authorization header present"),
        ({}, "private", False, "response Cache-Control is private or no-store"),
        ({}, None, True, "response sets a cookie"),
    ],
)
def test_each_skip_is_logged_at_debug(
    caplog: pytest.LogCaptureFixture,
    headers: dict[str, str],
    cache_control: str | None,
    set_cookie: bool,
    expected: str,
):
    app = FastAPI()

    @app.get("/logged")
    @cache(ttl=60)
    async def logged(response: Response):
        if cache_control is not None:
            response.headers["Cache-Control"] = cache_control
        if set_cookie:
            response.set_cookie("c", "1")
        return {}

    with caplog.at_level(logging.DEBUG, logger="fastapi_cachex.cache"):
        TestClient(app).get("/logged", headers=headers)

    assert any(
        record.levelno == logging.DEBUG and expected in record.getMessage()
        for record in caplog.records
    )


async def test_set_cookie_on_a_public_route_is_sent_as_private():
    """A shared cache downstream must not store the cookie response either:
    `public` becomes `private`, the other directives stay."""
    app = FastAPI()

    @app.get("/banner")
    @cache(
        ttl=60,
        public=True,
        must_revalidate=True,
        stale="revalidate",
        stale_ttl=30,
        immutable=True,
    )
    async def banner(response: Response):
        response.set_cookie("seen", "1")
        return {"banner": True}

    client = TestClient(app)
    first = client.get("/banner")
    again = client.get("/banner", headers={"If-None-Match": first.headers["ETag"]})

    expected = (
        "private, max-age=60, must-revalidate, stale-while-revalidate=30, immutable"
    )
    assert first.headers["Cache-Control"] == expected
    # Not stored: the handler ran again, and its fresh cookie response is a 200.
    assert again.status_code == 200
    assert again.headers["Cache-Control"] == expected
    assert await BackendProxy.get().get(_key("/banner")) is None


def test_set_cookie_304_on_a_bypassed_route_is_private():
    """The bypass path's 304 also carries the private variant."""
    app = FastAPI()

    @app.get("/uncached")
    @cache(public=True)
    async def uncached(response: Response):
        response.set_cookie("seen", "1")
        return {"uncached": True}

    client = TestClient(app)
    first = client.get("/uncached")
    revalidated = client.get(
        "/uncached", headers={"If-None-Match": first.headers["ETag"]}
    )

    assert first.headers["Cache-Control"] == "private"
    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == "private"


async def test_set_cookie_on_a_no_cache_route_is_private_no_cache():
    """`no_cache` omits the scope, so `private` is added in front."""
    app = FastAPI()

    @app.get("/ticker")
    @cache(ttl=60, no_cache=True, public=True, must_revalidate=True)
    async def ticker(response: Response):
        response.set_cookie("seen", "1")
        return {"ticker": 1}

    client = TestClient(app)
    first = client.get("/ticker")
    revalidated = client.get(
        "/ticker", headers={"If-None-Match": first.headers["ETag"]}
    )

    assert first.headers["Cache-Control"] == "private, no-cache, must-revalidate"
    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == (
        "private, no-cache, must-revalidate"
    )
    assert await BackendProxy.get().get(_key("/ticker")) is None


async def test_must_revalidate_does_not_lift_the_authorization_bypass():
    """RFC 9111 §3.5 would allow reuse under `must-revalidate`; the library
    requires `public` or `cache_authorized`, and sends `private`."""
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/account")
    @cache(ttl=60, must_revalidate=True)
    async def account(request: Request):
        calls["n"] += 1
        return {"for": request.headers["authorization"]}

    client = TestClient(app)
    alice = client.get("/account", headers={"Authorization": "Bearer alice"})
    bob = client.get("/account", headers={"Authorization": "Bearer bob"})
    revalidated = client.get(
        "/account",
        headers={"Authorization": "Bearer bob", "If-None-Match": bob.headers["ETag"]},
    )

    assert alice.json() == {"for": "Bearer alice"}
    assert bob.json() == {"for": "Bearer bob"}
    assert bob.headers["Cache-Control"] == "private, max-age=60, must-revalidate"
    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == (
        "private, max-age=60, must-revalidate"
    )
    assert calls["n"] == 3
    assert await BackendProxy.get().get(_key("/account")) is None


def test_authorization_on_a_no_cache_route_is_private_no_cache():
    app = FastAPI()

    @app.get("/live")
    @cache(ttl=60, no_cache=True)
    async def live():
        return {"live": True}

    client = TestClient(app)
    auth = {"Authorization": "Bearer alice"}
    first = client.get("/live", headers=auth)
    revalidated = client.get(
        "/live", headers={**auth, "If-None-Match": first.headers["ETag"]}
    )

    assert first.headers["Cache-Control"] == "private, no-cache"
    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == "private, no-cache"


@pytest.mark.parametrize(
    ("public", "cache_authorized", "expected"),
    [
        (True, False, "public, max-age=60"),
        (False, True, "private, max-age=60"),
    ],
)
def test_opted_in_authorization_header(
    public: bool, cache_authorized: bool, expected: str
):
    """``public`` keeps the decorator's header; ``cache_authorized`` adds ``private`` (#372)."""
    app = FastAPI()

    @app.get("/opted-in")
    @cache(ttl=60, public=public, cache_authorized=cache_authorized)
    async def opted_in():
        return {"ok": True}

    client = TestClient(app)
    auth = {"Authorization": "Bearer alice"}
    client.get("/opted-in", headers=auth)
    hit = client.get("/opted-in", headers=auth)

    assert hit.headers["Cache-Control"] == expected


def test_no_store_decorator_wins_over_set_cookie():
    app = FastAPI()

    @app.get("/nothing")
    @cache(no_store=True, public=True)
    async def nothing(response: Response):
        response.set_cookie("c", "1")
        return {}

    assert TestClient(app).get("/nothing").headers["Cache-Control"] == "no-store"
