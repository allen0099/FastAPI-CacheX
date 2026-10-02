"""A cache miss answers a matching `If-None-Match` with 304 (#237).

After an entry expires or is cleared, a client revalidating its copy sends the
ETag it has. The handler runs and renders the same content, so the fresh ETag
matches: that used to be a full 200 (and the next request a 304); it is a 304
now, as on the hit, bypass and no_cache paths. The entry is stored again either
way. Every 304 for a fresh render keeps what the handler did beyond the body:
its cookies and its background task (#233).
"""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask

from fastapi_cachex.cache import cache
from fastapi_cachex.proxy import BackendProxy


def _app(**cache_kwargs: object) -> tuple[TestClient, list[int]]:
    app = FastAPI()
    calls: list[int] = []

    @app.get("/item")
    @cache(ttl=60, **cache_kwargs)  # type: ignore[arg-type]
    async def item():
        calls.append(1)
        return {"item": 1}

    return TestClient(app), calls


async def test_a_miss_answers_a_matching_etag_with_304() -> None:
    client, calls = _app()
    etag = client.get("/item").headers["ETag"]
    await BackendProxy.get().clear()

    response = client.get("/item", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["ETag"] == etag
    assert response.headers["Cache-Control"] == "max-age=60"
    assert "Age" not in response.headers
    assert len(calls) == 2
    # Stored again: the next request is a hit and does not run the handler.
    assert client.get("/item").status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize(
    "header",
    ["*", '"{tag}"', '"other", W/"{tag}"'],
    ids=["star", "strong-form", "list"],
)
async def test_a_miss_matches_the_if_none_match_forms(header: str) -> None:
    client, _ = _app()
    etag = client.get("/item").headers["ETag"]
    await BackendProxy.get().clear()

    tag = etag.removeprefix("W/").strip('"')
    response = client.get("/item", headers={"If-None-Match": header.format(tag=tag)})

    assert response.status_code == 304


async def test_a_miss_with_a_different_etag_is_a_200() -> None:
    client, _ = _app()
    client.get("/item")
    await BackendProxy.get().clear()

    response = client.get("/item", headers={"If-None-Match": '"stale"'})

    assert response.status_code == 200
    assert response.json() == {"item": 1}


async def test_a_miss_304_keeps_vary() -> None:
    client, _ = _app(vary=["Accept-Language"])
    etag = client.get("/item").headers["ETag"]
    await BackendProxy.get().clear()

    response = client.get("/item", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["Vary"] == "Accept-Language"


def test_a_miss_304_runs_the_handlers_background_task() -> None:
    app = FastAPI()
    ran: list[str] = []

    @app.get("/task")
    @cache(ttl=60)
    async def task():
        # Not stored: the handler marks it private, so every request is a miss.
        return Response(
            content=b"x",
            media_type="text/plain",
            headers={"Cache-Control": "private, max-age=5"},
            background=BackgroundTask(ran.append, "ran"),
        )

    client = TestClient(app)
    etag = client.get("/task").headers["ETag"]
    response = client.get("/task", headers={"If-None-Match": etag})

    assert response.status_code == 304
    # The handler's own header wins over the decorator's, as on the 200.
    assert response.headers["Cache-Control"] == "private, max-age=5"
    assert ran == ["ran", "ran"]


@pytest.mark.parametrize(
    "cache_kwargs",
    [{"ttl": 60}, {"public": True}, {"ttl": 60, "no_cache": True}],
    ids=["miss", "bypass", "no-cache"],
)
def test_a_fresh_304_keeps_the_handlers_cookie(cache_kwargs: dict[str, object]) -> None:
    app = FastAPI()
    calls: list[int] = []

    @app.get("/cookie")
    @cache(**cache_kwargs)  # type: ignore[arg-type]
    async def cookie(response: Response):
        calls.append(1)
        response.set_cookie("seen", str(len(calls)))
        response.set_cookie("other", "x")
        return {}

    client = TestClient(app)
    etag = client.get("/cookie").headers["ETag"]
    response = client.get("/cookie", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["Cache-Control"].startswith("private")
    # Both lines of the second render, not a stored copy.
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert any(c.startswith("seen=2") for c in cookies)
    assert any(c.startswith("other=x") for c in cookies)


@pytest.mark.parametrize(
    ("cache_kwargs", "handler_header", "expected"),
    [
        ({"ttl": 60, "public": True}, "public, max-age=99", "private, max-age=60"),
        ({}, None, "private"),
    ],
    ids=["handler-public-header", "bare-cache"],
)
def test_a_304_with_a_cookie_is_always_private(
    cache_kwargs: dict[str, object], handler_header: str | None, expected: str
) -> None:
    app = FastAPI()

    @app.get("/cookie")
    @cache(**cache_kwargs)  # type: ignore[arg-type]
    async def cookie(response: Response):
        response.set_cookie("seen", "1")
        if handler_header is not None:
            response.headers["Cache-Control"] = handler_header
        return {}

    client = TestClient(app)
    etag = client.get("/cookie").headers["ETag"]
    response = client.get("/cookie", headers={"If-None-Match": etag})

    assert response.status_code == 304
    # The cookie must never reach a shared cache on a public 304.
    assert response.headers["Cache-Control"] == expected
    assert "seen=1" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    "cache_kwargs",
    [{"public": True}, {"ttl": 60, "no_cache": True}],
    ids=["bypass", "no-cache"],
)
def test_a_fresh_304_runs_the_handlers_background_task(
    cache_kwargs: dict[str, object],
) -> None:
    app = FastAPI()
    ran: list[str] = []

    @app.get("/task")
    @cache(**cache_kwargs)  # type: ignore[arg-type]
    async def task():
        return Response(
            content=b"x",
            media_type="text/plain",
            background=BackgroundTask(ran.append, "ran"),
        )

    client = TestClient(app)
    etag = client.get("/task").headers["ETag"]
    response = client.get("/task", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert ran == ["ran", "ran"]


def test_an_authorized_request_revalidates_with_304_and_private() -> None:
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, cache_authorized=True)
    async def me(request: Request):
        return {"user": request.headers["Authorization"]}

    client = TestClient(app)
    headers = {"Authorization": "Bearer t"}
    etag = client.get("/me", headers=headers).headers["ETag"]
    response = client.get("/me", headers={**headers, "If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["Cache-Control"].startswith("private")


async def test_a_miss_304_repeats_content_location_and_expires() -> None:
    app = FastAPI()

    @app.get("/doc")
    @cache(ttl=60)
    async def doc():
        return Response(
            content=b"x",
            media_type="text/plain",
            headers={
                "Content-Location": "/doc/1",
                "Expires": "Thu, 01 Jan 2099 00:00:00 GMT",
            },
        )

    client = TestClient(app)
    etag = client.get("/doc").headers["ETag"]
    await BackendProxy.get().clear()
    response = client.get("/doc", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["Content-Location"] == "/doc/1"
    assert response.headers["Expires"] == "Thu, 01 Jan 2099 00:00:00 GMT"
