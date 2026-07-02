"""Tests for the cache.invalidate() helper."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.cache import cache
from fastapi_cachex.cache import invalidate
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

app = FastAPI()
client = TestClient(app)

call_count = {"value": 0}


@app.get("/invalidate-target")
@cache(ttl=60)
async def invalidate_target() -> dict[str, int]:
    call_count["value"] += 1
    return {"calls": call_count["value"]}


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    call_count["value"] = 0
    BackendProxy.set(MemoryBackend())
    yield
    BackendProxy.set(None)


def _build_request(path: str, host: str = "testserver") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [(b"host", host.encode())],
    }
    return Request(scope)


def test_invalidate_forces_re_execution_on_next_request() -> None:
    response1 = client.get("/invalidate-target")
    assert response1.status_code == 200
    assert response1.json() == {"calls": 1}

    # Cache hit: handler is not re-invoked.
    response2 = client.get("/invalidate-target")
    assert response2.status_code == 200
    assert response2.json() == {"calls": 1}

    removed = asyncio.run(invalidate(_build_request("/invalidate-target")))
    assert removed is True

    # Cache miss after invalidation: handler runs again.
    response3 = client.get("/invalidate-target")
    assert response3.status_code == 200
    assert response3.json() == {"calls": 2}


def test_invalidate_returns_false_for_missing_key() -> None:
    removed = asyncio.run(invalidate(_build_request("/does-not-exist")))
    assert removed is False


def test_invalidate_returns_false_when_no_backend_configured() -> None:
    BackendProxy.set(None)
    removed = asyncio.run(invalidate(_build_request("/invalidate-target")))
    assert removed is False


def test_invalidate_with_custom_key_builder() -> None:
    def custom_key_builder(request: Request) -> str:
        return f"custom:{request.url.path}"

    async def _run() -> None:
        backend = BackendProxy.get()
        await backend.set("custom:/x", CacheEntry(fingerprint="e", content=b"v"))

        removed = await invalidate(_build_request("/x"), key_builder=custom_key_builder)

        assert removed is True
        assert await backend.get("custom:/x") is None

    asyncio.run(_run())
