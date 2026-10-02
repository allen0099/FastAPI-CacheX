"""`@cache` arguments that send no directive or contradict each other (#328, #363).

A bare `@cache()` has no directive to send: it used to set an empty
`Cache-Control` header, replacing the handler's own one. It now keeps the
handler's header and only adds the ETag, which makes it an ETag-only mode, so
it does not warn. `no_store` overrides every other caching argument, so passing
one with it warns.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi import Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from fastapi_cachex.cache import cache


def _bare_app(handler_cache_control: str | None = None, **kwargs: object) -> TestClient:
    app = FastAPI()

    @app.get("/bare")
    @cache(**kwargs)  # type: ignore[arg-type]
    async def bare(response: Response):
        if handler_cache_control is not None:
            response.headers["Cache-Control"] = handler_cache_control
        return {"ok": True}

    return TestClient(app)


def test_bare_cache_sends_no_cache_control() -> None:
    client = _bare_app()

    first = client.get("/bare")
    assert "Cache-Control" not in first.headers
    assert "ETag" in first.headers

    revalidated = client.get("/bare", headers={"If-None-Match": first.headers["ETag"]})
    assert revalidated.status_code == 304
    assert "Cache-Control" not in revalidated.headers


@pytest.mark.parametrize("handler_header", ["max-age=30", "private, max-age=30"])
def test_bare_cache_keeps_the_handlers_cache_control(handler_header: str) -> None:
    client = _bare_app(handler_header)

    first = client.get("/bare")
    assert first.headers["Cache-Control"] == handler_header

    revalidated = client.get("/bare", headers={"If-None-Match": first.headers["ETag"]})
    assert revalidated.status_code == 304
    assert revalidated.headers["Cache-Control"] == handler_header


def test_bare_cache_with_vary_sends_vary_and_no_cache_control() -> None:
    response = _bare_app(vary=["Accept-Language"]).get("/bare")

    assert "Cache-Control" not in response.headers
    assert response.headers["Vary"] == "Accept-Language"


def test_bare_cache_streaming_response_gets_no_cache_control() -> None:
    app = FastAPI()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"x"

    @app.get("/stream")
    @cache()
    async def stream():
        return StreamingResponse(chunks(), media_type="text/plain")

    assert "Cache-Control" not in TestClient(app).get("/stream").headers


def test_bare_cache_still_sends_private_for_a_cookie() -> None:
    app = FastAPI()

    @app.get("/cookie")
    @cache()
    async def cookie(response: Response):
        response.set_cookie("c", "1")
        response.headers["Cache-Control"] = "max-age=30"
        return {}

    # As on every route: a response that sets a cookie is sent with the
    # decorator's private header, in place of the handler's own one.
    assert TestClient(app).get("/cookie").headers["Cache-Control"] == "private"


def test_bare_cache_still_sends_private_for_a_credential_request() -> None:
    response = _bare_app("max-age=30").get(
        "/bare", headers={"Authorization": "Bearer t"}
    )

    assert response.headers["Cache-Control"] == "private"


@pytest.mark.parametrize(
    ("kwargs", "ignored"),
    [
        ({"ttl": 60}, "ttl"),
        ({"ttl": 0}, "ttl"),
        ({"stale": "revalidate", "stale_ttl": 30}, "stale, stale_ttl"),
        ({"no_cache": True}, "no_cache"),
        ({"public": True}, "public"),
        ({"private": True}, "private"),
        ({"immutable": True}, "immutable"),
        ({"must_revalidate": True}, "must_revalidate"),
        ({"ttl": 60, "public": True}, "ttl, public"),
    ],
)
def test_no_store_warns_about_the_arguments_it_overrides(
    kwargs: dict[str, object], ignored: str
) -> None:
    app = FastAPI()

    with pytest.warns(
        UserWarning, match=f"cache no_store ignores {ignored}:"
    ) as record:

        @app.get("/none")
        @cache(no_store=True, **kwargs)  # type: ignore[arg-type]
        async def none():
            return {}

    assert len(record) == 1
    assert record[0].filename == __file__
    # The warning changes nothing at request time.
    assert TestClient(app).get("/none").headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"no_store": True},
        {"ttl": 60},
        {"ttl": 0},
        {"public": True},
        {"no_cache": True},
        {"must_revalidate": True},
        {"private": True},
        {"immutable": True},
        {"stale": "revalidate", "stale_ttl": 30},
        {},
        {"vary": ["Accept-Language"]},
        {"ttl": 60, "public": False, "no_cache": False},
    ],
)
def test_arguments_that_do_something_do_not_warn(kwargs: dict[str, object]) -> None:
    # filterwarnings = error turns any warning into a failure here.
    @cache(**kwargs)  # type: ignore[arg-type]
    async def handler():
        return {}
