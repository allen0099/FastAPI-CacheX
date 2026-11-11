"""``X-Cache-Reason`` and ``CacheEvent.reason``: why a request was not cached."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from typing import get_args

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheEvent
from fastapi_cachex import CacheReason
from fastapi_cachex import invalidate_tags
from fastapi_cachex import set_event_handler
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.cache import cache
from fastapi_cachex.types import CacheEntry


def _client(response: Any = None, **cache_kwargs: Any) -> TestClient:
    app = FastAPI()
    cache_kwargs.setdefault("debug_header", True)

    @app.api_route("/items", methods=["GET", "HEAD"])
    @cache(**cache_kwargs)
    async def items(request: Request) -> Any:
        return {"items": [1]} if response is None else response()

    return TestClient(app)


def _reason(response: Any) -> str | None:
    return response.headers.get("x-cache-reason")  # type: ignore[no-any-return]


def _with_cookie() -> Response:
    response = Response(b"{}", media_type="application/json")
    response.set_cookie("visit", "1")
    return response


@pytest.mark.parametrize(
    ("cache_kwargs", "headers", "reason"),
    [
        ({"no_store": True}, {}, "no-store"),
        ({"ttl": 60, "private": True}, {}, "private"),
        ({}, {}, "no-ttl"),
        (
            {"ttl": 60},
            {"Authorization": "Bearer t"},
            "authorization",
        ),
        (
            {"ttl": 60, "client_cache_control": True},
            {"Cache-Control": "no-store"},
            "request-no-store",
        ),
    ],
)
def test_a_bypass_says_why(
    cache_kwargs: dict[str, Any], headers: dict[str, str], reason: str
) -> None:
    response = _client(**cache_kwargs).get("/items", headers=headers)

    assert response.headers["x-cache"] == "BYPASS"
    assert _reason(response) == reason


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (lambda: Response(b"{}", status_code=404), "status"),
        (lambda: StreamingResponse(iter([b"a"])), "streamed"),
        (_with_cookie, "set-cookie"),
        (
            lambda: Response(b"{}", headers={"Cache-Control": "private"}),
            "private-response",
        ),
    ],
)
def test_a_miss_that_is_not_stored_says_why(build: Any, reason: str) -> None:
    client = _client(build, ttl=60)

    for _ in range(2):  # not stored, so the second request says the same
        response = client.get("/items")
        assert response.headers["x-cache"] == "MISS"
        assert _reason(response) == reason


def test_a_head_miss_says_only_get_is_stored() -> None:
    response = _client(ttl=60).head("/items")

    assert response.headers["x-cache"] == "MISS"
    assert _reason(response) == "head"


def test_a_cookie_set_by_a_dependency_is_named() -> None:
    app = FastAPI()

    def visit(response: Response) -> None:
        response.set_cookie("visit", "1")

    @app.get("/items", dependencies=[Depends(visit)])
    @cache(ttl=60, debug_header=True)
    async def items() -> dict[str, int]:
        return {"n": 1}

    assert _reason(TestClient(app).get("/items")) == "set-cookie"


class _FailingWrites(MemoryBackend):
    async def set(self, key: str, value: CacheEntry, ttl: Any = None) -> None:
        raise ConnectionError


def test_a_failed_write_is_named() -> None:
    BackendProxy.set(_FailingWrites())

    response = _client(ttl=60).get("/items")

    assert response.status_code == 200
    assert _reason(response) == "write-failed"


def test_a_stored_miss_and_a_hit_have_no_reason() -> None:
    client = _client(ttl=60)

    assert _reason(client.get("/items")) is None
    hit = client.get("/items")
    assert hit.headers["x-cache"] == "HIT"
    assert _reason(hit) is None


def test_no_reason_header_without_debug_header() -> None:
    response = _client(no_store=True, debug_header=False).get("/items")

    assert _reason(response) is None


@pytest.fixture
def events() -> Iterator[list[CacheEvent]]:
    seen: list[CacheEvent] = []
    set_event_handler(seen.append)
    yield seen
    set_event_handler(None)


def test_the_event_carries_the_reason(events: list[CacheEvent]) -> None:
    client = _client(_with_cookie, ttl=60, debug_header=False)

    client.get("/items")
    client.get("/items", headers={"Authorization": "Bearer t"})

    answered = [(e.kind, e.reason) for e in events if e.duration is not None]
    assert answered == [
        ("miss", "set-cookie"),
        ("bypass", "authorization"),
    ]


@pytest.mark.parametrize(
    "page", ["docs/HTTP_CACHING.md", "i18n/zh-TW/docs/HTTP_CACHING.md"]
)
def test_the_docs_list_exactly_the_reasons(page: str) -> None:
    """``CacheReason`` is public: the table and the type name the same values."""
    text = (Path(__file__).parent.parent / page).read_text(encoding="utf-8")
    table = text.split("{#why-not-cached}")[1].split("\n\n")[2]
    rows = [row.split("|")[1] for row in table.splitlines()[2:]]
    listed = [code for row in rows for code in re.findall(r"`([a-z-]+)`", row)]

    assert sorted(listed) == sorted(get_args(CacheReason))


def test_session_data_is_named() -> None:
    client = _client(ttl=60)
    inner = client.app

    async def with_session(scope: Any, receive: Any, send: Any) -> None:
        scope["session"] = {"user": 1}
        await inner(scope, receive, send)

    response = TestClient(with_session).get("/items")

    assert _reason(response) == "session"


def test_a_status_set_by_a_dependency_is_named() -> None:
    app = FastAPI()

    def created(response: Response) -> None:
        response.status_code = 201

    @app.get("/items", dependencies=[Depends(created)])
    @cache(ttl=60, debug_header=True)
    async def items() -> dict[str, int]:
        return {"n": 1}

    assert _reason(TestClient(app).get("/items")) == "dependency-status"


class _FailingBatchReads(MemoryBackend):
    async def get_many(self, keys: Any) -> dict[str, CacheEntry]:
        raise ConnectionError


def test_unreadable_tag_versions_are_named() -> None:
    BackendProxy.set(_FailingBatchReads())

    response = _client(ttl=60, tags=["items"]).get("/items")

    assert response.status_code == 200
    assert _reason(response) == "tag-read-failed"


def test_a_tag_invalidated_while_the_handler_ran_is_named() -> None:
    app = FastAPI()

    @app.get("/items")
    @cache(ttl=60, tags=["items"], debug_header=True)
    async def items() -> dict[str, int]:
        await invalidate_tags("items")
        return {"n": 1}

    assert _reason(TestClient(app).get("/items")) == "tag-invalidated"
