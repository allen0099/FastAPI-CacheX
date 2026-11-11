"""``@cache(client_cache_control=...)`` lets a request skip the entry (#541)."""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError


def _app(**cache_kwargs: Any) -> tuple[TestClient, list[int]]:
    """A route that returns how often its handler ran."""
    app = FastAPI()
    runs: list[int] = []
    cache_kwargs.setdefault("debug_header", True)

    @app.get("/count")
    @cache(ttl=60, **cache_kwargs)
    async def count() -> dict[str, int]:
        runs.append(1)
        return {"runs": len(runs)}

    return TestClient(app), runs


def _get(client: TestClient, cache_control: str | None = None) -> tuple[int, str]:
    headers = {} if cache_control is None else {"Cache-Control": cache_control}
    response = client.get("/count", headers=headers)
    return response.json()["runs"], response.headers["x-cache"]


@pytest.mark.parametrize("directive", ["no-cache", "no-store", "max-age=0"])
def test_ignored_by_default(directive: str) -> None:
    client, _ = _app()

    assert _get(client) == (1, "MISS")
    assert _get(client, directive) == (1, "HIT")


@pytest.mark.parametrize(
    "header", ["no-cache", "max-age=0", "No-Cache", "max-age = 0", "private, no-cache"]
)
def test_no_cache_runs_the_handler_and_replaces_the_entry(header: str) -> None:
    client, _ = _app(client_cache_control=True)

    assert _get(client) == (1, "MISS")
    assert _get(client, header) == (2, "REFRESH")
    # The next caller gets what the refresh stored.
    assert _get(client) == (2, "HIT")


def test_no_store_runs_the_handler_and_leaves_the_entry() -> None:
    client, _ = _app(client_cache_control=True)

    assert _get(client) == (1, "MISS")
    assert _get(client, "no-store") == (2, "BYPASS")
    assert _get(client) == (1, "HIT")


def test_no_store_on_a_cold_key_stores_nothing() -> None:
    client, _ = _app(client_cache_control=True)

    assert _get(client, "no-store, no-cache") == (1, "BYPASS")
    assert _get(client) == (2, "MISS")


@pytest.mark.parametrize("header", ["max-age=5", "max-stale", "only-if-cached", ""])
def test_other_request_directives_stay_ignored(header: str) -> None:
    client, _ = _app(client_cache_control=True)

    assert _get(client) == (1, "MISS")
    assert _get(client, header) == (1, "HIT")


def test_a_callable_decides_per_request() -> None:
    asked: list[str] = []

    def staff_only(request: Request) -> bool:
        asked.append(request.headers.get("x-role", ""))
        return request.headers.get("x-role") == "staff"

    client, _ = _app(client_cache_control=staff_only)

    assert _get(client) == (1, "MISS")
    # Not called for a request that asks for nothing.
    assert asked == []
    assert _get(client, "no-cache") == (1, "HIT")
    refreshed = client.get(
        "/count", headers={"Cache-Control": "no-cache", "X-Role": "staff"}
    )
    assert (refreshed.json()["runs"], refreshed.headers["x-cache"]) == (2, "REFRESH")
    assert asked == ["", "staff"]


def test_a_refresh_still_answers_a_matching_etag_with_304() -> None:
    app = FastAPI()

    @app.get("/same")
    @cache(ttl=60, client_cache_control=True, debug_header=True)
    async def same() -> dict[str, str]:
        return {"always": "this"}

    client = TestClient(app)
    etag = client.get("/same").headers["etag"]

    response = client.get(
        "/same", headers={"Cache-Control": "no-cache", "If-None-Match": etag}
    )

    assert response.status_code == 304
    assert response.headers["x-cache"] == "REFRESH"


def test_a_route_that_skips_the_backend_is_unchanged() -> None:
    client, _ = _app(client_cache_control=True, private=True)

    assert _get(client, "no-cache") == (1, "BYPASS")


@pytest.mark.parametrize("value", [1, "yes", None])
def test_anything_but_a_bool_or_a_callable_is_rejected(value: Any) -> None:
    with pytest.raises(CacheXError, match="client_cache_control must be a bool"):
        _app(client_cache_control=value)


def test_no_store_route_warns_that_it_is_ignored() -> None:
    with pytest.warns(UserWarning, match="client_cache_control"):
        _app(no_store=True, client_cache_control=True)


def test_an_async_callable_is_rejected() -> None:
    """Its coroutine is truthy: it would let every client through."""

    async def allow(request: Request) -> bool:
        return False

    with pytest.raises(CacheXError, match="plain function, not async"):
        _app(client_cache_control=allow)


@pytest.mark.parametrize("answer", ["no", 1, [0], None])
def test_only_true_itself_opens_the_route(answer: Any) -> None:
    client, _ = _app(client_cache_control=lambda request: answer)

    assert _get(client) == (1, "MISS")
    assert _get(client, "no-cache") == (1, "HIT")


def test_every_cache_control_line_is_read() -> None:
    client, _ = _app(client_cache_control=True)
    assert _get(client) == (1, "MISS")

    response = client.get(
        "/count",
        headers=[("Cache-Control", "max-age=5"), ("Cache-Control", "no-cache")],
    )

    assert (response.json()["runs"], response.headers["x-cache"]) == (2, "REFRESH")


def test_a_refresh_that_is_not_stored_leaves_the_entry() -> None:
    app = FastAPI()
    fail: list[int] = []

    @app.get("/flaky")
    @cache(ttl=60, client_cache_control=True, debug_header=True)
    async def flaky() -> Response:
        if fail:
            return Response(content=b"down", status_code=503)
        return Response(content=b"up")

    client = TestClient(app)
    assert client.get("/flaky").text == "up"
    fail.append(1)

    assert (
        client.get("/flaky", headers={"Cache-Control": "no-cache"}).status_code == 503
    )
    assert client.get("/flaky").text == "up"
