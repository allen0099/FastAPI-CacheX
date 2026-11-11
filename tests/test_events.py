"""``set_event_handler()`` is told what the cache does (#540)."""

import asyncio
import inspect
import logging
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import BackendUnavailableError
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


def test_the_route_keeps_the_prefix_it_was_included_or_mounted_under(
    events: list[CacheEvent],
) -> None:
    """A router's own part alone would collide between routers."""
    inner = APIRouter()

    @inner.get("/items/{item_id}")
    @cache(ttl=60)
    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    outer = APIRouter(prefix="/v1")
    outer.include_router(inner, prefix="/shops/{shop}")
    sub = FastAPI()
    sub.include_router(inner)
    app = FastAPI()
    app.include_router(outer, prefix="/api")
    app.mount("/sub", sub)
    client = TestClient(app)

    assert client.get("/api/v1/shops/acme/items/7").status_code == 200
    assert client.get("/sub/items/7").status_code == 200

    assert {event.route for event in events} == {
        "/api/v1/shops/{shop}/items/{item_id}",
        "/sub/items/{item_id}",
    }


def test_a_request_whose_handler_raised_is_still_reported(
    events: list[CacheEvent],
) -> None:
    app = FastAPI()

    @app.get("/gone")
    @cache(ttl=60)
    async def gone() -> None:
        raise HTTPException(status_code=404)

    assert TestClient(app).get("/gone").status_code == 404

    assert [(e.kind, e.route) for e in events] == [("miss", "/gone")]
    assert events[0].duration is not None


async def test_callers_waiting_for_one_computation_are_hits(
    events: list[CacheEvent],
) -> None:
    manager = CacheManager(BackendProxy.get())

    async def slow() -> int:
        await asyncio.sleep(0.01)
        return 1

    await asyncio.gather(*(manager.get_or_set("k", slow, ttl=60) for _ in range(5)))

    assert sorted(_kinds(events)) == ["hit", "hit", "hit", "hit", "miss", "store"]


@pytest.mark.parametrize("handler", ["not callable", 1])
def test_a_handler_that_is_not_a_function_is_rejected(handler: Any) -> None:
    with pytest.raises(TypeError, match="plain function"):
        set_event_handler(handler)


def test_an_async_handler_is_rejected() -> None:
    """Its coroutine would never be awaited: nothing would be recorded."""

    async def record(event: CacheEvent) -> None: ...

    with pytest.raises(TypeError, match="plain function"):
        set_event_handler(record)  # type: ignore[arg-type]


class _Unreachable(MemoryBackend):
    """Every backend call fails as a server that cannot be reached does."""

    def __getattribute__(self, name: str) -> Any:
        value = super().__getattribute__(name)
        if name.startswith("_") or not inspect.iscoroutinefunction(value):
            return value
        if name == "aclose":
            return value

        async def down(*args: Any, **kwargs: Any) -> Any:
            raise BackendUnavailableError

        return down


_KEY_CALLS = {
    "get": lambda m: m.get("user:1"),
    "set": lambda m: m.set("user:1", 1),
    "set by keyword": lambda m: m.set(key="user:1", value=1),
    "add": lambda m: m.add("user:1", 1),
    "delete": lambda m: m.delete("user:1"),
    "increment": lambda m: m.increment("user:1"),
    "has": lambda m: m.has("user:1"),
    "get_or_set": lambda m: m.get_or_set("user:1", lambda: 1, ttl=60),
}
_OTHER_CALLS = {
    "get_many": lambda m: m.get_many(["user:1", "user:2"]),
    "set_many": lambda m: m.set_many({"user:1": 1}),
    "clear_pattern": lambda m: m.clear_pattern("user:*"),
    "clear_prefix": lambda m: m.clear_prefix("user:"),
    # Goes through clear_prefix(): reported once all the same.
    "clear": lambda m: m.clear(),
    "invalidate_tags": lambda m: m.invalidate_tags("users"),
}


@pytest.mark.parametrize("call", _KEY_CALLS.values(), ids=list(_KEY_CALLS))
async def test_a_call_about_one_key_reports_it_once_and_still_raises(
    events: list[CacheEvent], call: Any
) -> None:
    with pytest.raises(BackendUnavailableError):
        await call(CacheManager(_Unreachable()))

    assert [(e.kind, e.source, e.key) for e in events] == [("error", "app", "user:1")]


@pytest.mark.parametrize("call", _OTHER_CALLS.values(), ids=list(_OTHER_CALLS))
async def test_a_call_about_several_keys_reports_once_without_a_key(
    events: list[CacheEvent], call: Any
) -> None:
    with pytest.raises(BackendUnavailableError):
        await call(CacheManager(_Unreachable()))

    assert [(e.kind, e.source, e.key) for e in events] == [("error", "app", None)]


async def test_a_failure_inside_a_factory_is_reported_by_the_call_that_failed(
    events: list[CacheEvent],
) -> None:
    """Not a second time by the ``get_or_set`` around it."""
    healthy = CacheManager(BackendProxy.get())
    down = CacheManager(_Unreachable())

    async def load() -> Any:
        return await down.get("inner")

    with pytest.raises(BackendUnavailableError):
        await healthy.get_or_set("outer", load, ttl=60)

    assert [(e.kind, e.key) for e in events] == [("miss", "outer"), ("error", "inner")]


async def test_a_cached_function_reports_an_unreachable_backend(
    events: list[CacheEvent],
) -> None:
    @cached(ttl=60, manager=CacheManager(_Unreachable()), key="answer")
    async def answer() -> int:
        return 42

    with pytest.raises(BackendUnavailableError):
        await answer()

    assert [(e.kind, e.source) for e in events] == [("error", "app")]


class _ReadOnly(MemoryBackend):
    """Reads work, writes fail."""

    async def set(self, key: str, value: CacheEntry, ttl: Any = None) -> None:
        raise BackendUnavailableError


@pytest.mark.parametrize("backend_class", [_Unreachable, _ReadOnly])
async def test_a_route_that_fails_closed_reports_the_error_too(
    events: list[CacheEvent], backend_class: type[MemoryBackend]
) -> None:
    """On the read, and on the write after the handler ran."""
    backend = backend_class()
    BackendProxy.set(backend)
    client = TestClient(_app(fail_open=False).app, raise_server_exceptions=False)
    try:
        assert client.get("/items/7").status_code == 500
    finally:
        await backend.aclose()

    assert [
        (e.kind, e.source, e.key, e.route) for e in events if e.kind == "error"
    ] == [("error", "http", KEY, "/items/{item_id}")]
