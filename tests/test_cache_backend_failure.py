"""`@cache` when the backend raises (#228).

A cache is an optimisation: by default a failing backend makes the route answer
uncached instead of turning every cached route into a 500.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import cache
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.types import CacheEntry
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import requires_memcached


class FailingBackend(MemoryBackend):
    """A memory backend whose reads and/or writes raise like a lost connection."""

    def __init__(self, *, fail_get: bool, fail_set: bool) -> None:
        super().__init__()
        self.fail_get = fail_get
        self.fail_set = fail_set

    async def get(self, key: str) -> CacheEntry | None:
        if self.fail_get:
            msg = "backend unreachable"
            raise ConnectionError(msg)
        return await super().get(key)

    async def set(self, key: str, value: CacheEntry, ttl: int | None = None) -> None:
        if self.fail_set:
            msg = "backend unreachable"
            raise ConnectionError(msg)
        await super().set(key, value, ttl)


def _counting_app(*, fail_open: bool = True) -> tuple[FastAPI, list[int]]:
    app = FastAPI()
    calls: list[int] = []

    @app.get("/item")
    @cache(ttl=60, fail_open=fail_open)
    async def item() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    return app, calls


@pytest.mark.parametrize(
    ("fail_get", "fail_set"),
    [(True, False), (False, True), (True, True)],
    ids=["get", "set", "both"],
)
def test_failing_backend_serves_the_handler_response(
    caplog: pytest.LogCaptureFixture, *, fail_get: bool, fail_set: bool
) -> None:
    """A read or write error is logged and the handler's response is served."""
    BackendProxy.set(FailingBackend(fail_get=fail_get, fail_set=fail_set))
    app, calls = _counting_app()
    client = TestClient(app)

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.cache"):
        first = client.get("/item")
        second = client.get("/item")

    assert first.status_code == 200
    assert first.json() == {"n": 1}
    assert "ETag" in first.headers
    assert first.headers["Cache-Control"] == "max-age=60"
    assert second.status_code == 200
    # Nothing could be served from the backend, so the handler ran again.
    assert len(calls) == 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    if fail_get:
        assert any("read failed" in m for m in warnings)
    if fail_set:
        assert any("write failed" in m for m in warnings)


def test_a_write_failure_does_not_hide_a_working_read() -> None:
    """Only the failing half is skipped: entries already stored are still served."""
    backend = FailingBackend(fail_get=False, fail_set=False)
    BackendProxy.set(backend)
    app, calls = _counting_app()
    client = TestClient(app)
    client.get("/item")

    backend.fail_set = True
    response = client.get("/item")

    assert response.json() == {"n": 1}
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("fail_get", "fail_set"), [(True, False), (False, True)], ids=["get", "set"]
)
def test_fail_open_false_propagates_the_error(
    *, fail_get: bool, fail_set: bool
) -> None:
    """Opting out lets the backend error fail the request."""
    BackendProxy.set(FailingBackend(fail_get=fail_get, fail_set=fail_set))
    app, _calls = _counting_app(fail_open=False)
    client = TestClient(app)

    with pytest.raises(ConnectionError, match="backend unreachable"):
        client.get("/item")


@requires_memcached
def test_response_over_the_memcached_item_size_is_served_unstored() -> None:
    """Memcached refuses items over 1 MB by default; the route must still answer."""
    BackendProxy.set(MemcachedBackend(servers=[MEMCACHED_SERVER]))
    app = FastAPI()
    body = "x" * (2 * 1024 * 1024)

    @app.get("/large", response_class=PlainTextResponse)
    @cache(ttl=60)
    async def large() -> PlainTextResponse:
        return PlainTextResponse(body)

    response = TestClient(app).get("/large")

    assert response.status_code == 200
    assert response.text == body
