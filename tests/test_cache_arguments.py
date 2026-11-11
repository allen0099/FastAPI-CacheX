"""`@cache` arguments that send no directive or contradict each other (#328, #363).

A bare `@cache()` has no directive to send: it used to set an empty
`Cache-Control` header, replacing the handler's own one. It now keeps the
handler's header and only adds the ETag, which makes it an ETag-only mode, so
it does not warn. `no_store` overrides every other caching argument, so passing
one with it warns.
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi import Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.exceptions import TTLTypeError
from fastapi_cachex.exceptions import TTLValueError


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
        ({"s_maxage": 60}, "s_maxage"),
        ({"proxy_revalidate": True}, "proxy_revalidate"),
        ({"no_transform": True}, "no_transform"),
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


def _cache_control(
    kwargs: dict[str, object], headers: dict[str, str] | None = None
) -> str:
    app = FastAPI()

    @app.get("/x")
    @cache(**kwargs)  # type: ignore[arg-type]
    async def x() -> dict[str, str]:
        return {}

    return TestClient(app).get("/x", headers=headers).headers["Cache-Control"]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"s_maxage": 300}, "s-maxage=300"),
        ({"s_maxage": 0}, "s-maxage=0"),
        ({"s_maxage": timedelta(minutes=5)}, "s-maxage=300"),
        ({"ttl": 60, "s_maxage": 300}, "max-age=60, s-maxage=300"),
        ({"proxy_revalidate": True}, "proxy-revalidate"),
        ({"no_transform": True}, "no-transform"),
        (
            {
                "ttl": 60,
                "public": True,
                "s_maxage": 300,
                "must_revalidate": True,
                "proxy_revalidate": True,
                "stale": "error",
                "stale_ttl": 30,
                "immutable": True,
                "no_transform": True,
            },
            (
                "public, max-age=60, s-maxage=300, must-revalidate, "
                "proxy-revalidate, stale-if-error=30, immutable, no-transform"
            ),
        ),
        # no_cache keeps the two that are not about freshness.
        (
            {"ttl": 60, "no_cache": True, "s_maxage": 300, "proxy_revalidate": True},
            "no-cache, proxy-revalidate",
        ),
        ({"no_cache": True, "no_transform": True}, "no-cache, no-transform"),
        # private keeps no-transform, which is not about shared caches.
        ({"private": True, "no_transform": True}, "private, no-transform"),
    ],
)
def test_the_shared_cache_directives_are_sent(
    kwargs: dict[str, object], expected: str
) -> None:
    """``s-maxage``, ``proxy-revalidate`` and ``no-transform`` (#144)."""
    assert _cache_control(kwargs) == expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {
                "ttl": 60,
                "s_maxage": 300,
                "proxy_revalidate": True,
                "no_transform": True,
            },
            "private, max-age=60, no-transform",
        ),
        (
            {"no_cache": True, "s_maxage": 300, "proxy_revalidate": True},
            "private, no-cache",
        ),
    ],
)
def test_a_response_for_one_caller_drops_what_speaks_to_shared_caches(
    kwargs: dict[str, object], expected: str
) -> None:
    """A request with credentials gets ``private``; a CDN must not store it (#144)."""
    assert _cache_control(kwargs, {"Authorization": "Bearer x"}) == expected


def test_a_hit_carries_the_shared_cache_directives() -> None:
    app = FastAPI()

    @app.get("/x")
    @cache(ttl=60, s_maxage=300)
    async def x() -> dict[str, str]:
        return {}

    client = TestClient(app)
    client.get("/x")

    assert client.get("/x").headers["Cache-Control"] == "max-age=60, s-maxage=300"


@pytest.mark.parametrize(
    "kwargs", [{"s_maxage": 60}, {"s_maxage": 0}, {"proxy_revalidate": True}]
)
def test_private_rejects_what_only_applies_to_shared_caches(
    kwargs: dict[str, object],
) -> None:
    name = next(iter(kwargs))

    with pytest.raises(CacheXError, match=f"{name} cannot be combined with private"):
        _cache_control({"private": True, **kwargs})


@pytest.mark.parametrize(
    ("value", "error"),
    [(-1, TTLValueError), (1.5, TTLTypeError), (True, TTLTypeError)],
)
def test_s_maxage_is_validated_like_ttl(value: object, error: type[Exception]) -> None:
    with pytest.raises(error, match="s_maxage"):
        cache(s_maxage=value)  # type: ignore[arg-type]


def test_a_response_that_sets_a_cookie_drops_what_speaks_to_shared_caches() -> None:
    """It is sent with ``private``, like a response to a request with credentials."""
    app = FastAPI()

    @app.get("/x")
    @cache(ttl=60, public=True, s_maxage=300, proxy_revalidate=True, no_transform=True)
    async def x(response: Response) -> dict[str, str]:
        response.set_cookie("seen", "1")
        return {}

    header = TestClient(app).get("/x").headers["Cache-Control"]

    assert header == "private, max-age=60, no-transform"


def test_a_public_route_keeps_s_maxage_for_a_request_with_credentials() -> None:
    """``public=True`` declares the response shareable, whoever asks (#144)."""
    header = _cache_control(
        {"ttl": 60, "public": True, "s_maxage": 300}, {"Authorization": "Bearer x"}
    )

    assert header == "public, max-age=60, s-maxage=300"
