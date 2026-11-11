"""``set_event_handler()`` is told what the cache does (#540)."""

import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import CacheEvent
from fastapi_cachex import CacheManager
from fastapi_cachex import cache
from fastapi_cachex import cached
from fastapi_cachex import invalidate_path
from fastapi_cachex import set_event_handler
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

KEY = "http:v2|GET|testserver|/items/7|"


@pytest.fixture
def events() -> list[CacheEvent]:
    seen: list[CacheEvent] = []
    set_event_handler(seen.append)
    return seen


def _kinds(events: list[CacheEvent]) -> list[str]:
    return [event.kind for event in events]


def _app(**cache_kwargs: Any) -> TestClient:
    app = FastAPI()

    @app.get("/items/{item_id}")
    @cache(ttl=60, **cache_kwargs)
    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    @app.post("/items/{item_id}")
    async def update(request: Request, item_id: int) -> dict[str, bool]:
        return {"removed": await invalidate_path(request, f"/items/{item_id}")}

    return TestClient(app)


def test_a_miss_then_a_hit_on_a_route(events: list[CacheEvent]) -> None:
    client = _app()

    client.get("/items/7")
    client.get("/items/7")

    assert _kinds(events) == ["store", "miss", "hit"]
    store, miss, hit = events
    assert {event.source for event in events} == {"http"}
    assert {event.key for event in events} == {KEY}
    # The template, not the path: a label that does not grow with the data.
    assert {event.route for event in events} == {"/items/{item_id}"}
    assert store.duration is None
    assert miss.duration is not None
    assert hit.duration is not None
    assert hit.duration >= 0


def test_a_bypass_has_no_key(events: list[CacheEvent]) -> None:
    client = _app(private=True)

    client.get("/items/7")

    assert [(e.kind, e.key, e.route) for e in events] == [
        ("bypass", None, "/items/{item_id}")
    ]


def test_methods_that_are_not_cached_report_nothing(events: list[CacheEvent]) -> None:
    app = FastAPI()

    @app.post("/items")
    @cache(ttl=60)
    async def create() -> dict[str, bool]:
        return {"ok": True}

    TestClient(app).post("/items")

    assert events == []


def test_invalidate_reports_the_key_it_removed(events: list[CacheEvent]) -> None:
    client = _app()
    client.get("/items/7")
    events.clear()

    assert client.post("/items/7").json() == {"removed": True}
    assert client.post("/items/7").json() == {"removed": False}

    assert [(e.kind, e.source, e.key) for e in events] == [("invalidate", "http", KEY)]


class _Broken(MemoryBackend):
    async def get(self, key: str) -> CacheEntry | None:
        msg = "backend is down"
        raise ConnectionError(msg)


async def test_a_backend_failure_is_an_error_event(events: list[CacheEvent]) -> None:
    backend = _Broken()
    BackendProxy.set(backend)
    try:
        _app().get("/items/7")
    finally:
        await backend.aclose()

    assert ("error", KEY, "/items/{item_id}") in [
        (e.kind, e.key, e.route) for e in events
    ]


async def test_cache_manager_reads_writes_and_deletes(events: list[CacheEvent]) -> None:
    manager = CacheManager(BackendProxy.get())

    await manager.get("user:1")
    await manager.set("user:1", {"id": 1})
    await manager.get("user:1")
    await manager.get("user:1", type_=int)  # does not fit: reads as a miss
    await manager.delete("user:1")
    await manager.delete("user:1")

    assert [(e.kind, e.source, e.key) for e in events] == [
        ("miss", "app", "user:1"),
        ("store", "app", "user:1"),
        ("hit", "app", "user:1"),
        ("miss", "app", "user:1"),
        ("invalidate", "app", "user:1"),
    ]


async def test_get_or_set_and_cached_report_each_call_once(
    events: list[CacheEvent],
) -> None:
    """Their own reads through ``get()`` are not reported a second time."""
    manager = CacheManager(BackendProxy.get())

    await manager.get_or_set("report", lambda: {"total": 1}, ttl=60)
    await manager.get_or_set("report", lambda: {"total": 2}, ttl=60)

    assert _kinds(events) == ["miss", "store", "hit"]
    events.clear()

    @cached(ttl=60, manager=manager, key="answer")
    async def answer() -> int:
        return 42

    await answer()
    await answer()

    assert [(e.kind, e.source) for e in events] == [
        ("miss", "app"),
        ("store", "app"),
        ("hit", "app"),
    ]


def test_a_handler_that_raises_is_logged_and_the_request_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(event: CacheEvent) -> None:
        msg = "metrics are down"
        raise RuntimeError(msg)

    set_event_handler(broken)
    client = _app()

    with caplog.at_level(logging.ERROR, logger="fastapi_cachex.events"):
        response = client.get("/items/7")

    assert response.json() == {"id": 7}
    assert "The cache event handler raised" in caplog.text


def test_removing_the_handler_stops_the_events(events: list[CacheEvent]) -> None:
    set_event_handler(None)

    _app().get("/items/7")

    assert events == []
