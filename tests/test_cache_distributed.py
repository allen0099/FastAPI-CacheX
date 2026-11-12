"""``@cache(coalesce="distributed")``: one render per key across workers (#558).

Another worker is played by the test itself: it holds the ``CacheLock`` of the
route's key on the shared backend, as a worker rendering that key would.
"""

import asyncio
import importlib
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request

from fastapi_cachex import _stored_response as stored_response
from fastapi_cachex._coalesce import _IN_FLIGHT
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.lock import CacheLock
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

try:  # Starlette's TestClient moved to httpx2; the `lowest` env still has httpx.
    import httpx2 as httpx  # type: ignore[import-not-found, unused-ignore]
except ImportError:  # pragma: no cover - depends on the installed starlette
    import httpx  # type: ignore[no-redef, import-not-found, unused-ignore]

# `fastapi_cachex.cache` is the decorator once the package is imported.
cache_module = importlib.import_module("fastapi_cachex.cache")

KEY = "http:v2|GET|testserver|/items|"
LOCK_KEY = f"lock:{KEY}"


class Handler:
    """Counts runs, and notes whether the key's lock was held during each."""

    def __init__(self) -> None:
        self.locked: list[bool] = []
        self.fail = False

    async def run(self) -> None:
        self.locked.append(await _locked())
        if self.fail:
            msg = "handler failed"
            raise RuntimeError(msg)


async def _locked() -> bool:
    return await BackendProxy.get().get(LOCK_KEY) is not None


def _other_worker() -> CacheLock:
    return CacheLock(KEY, backend=BackendProxy.get())


class Polls:
    """Counts the times a request looked whether another worker was done."""

    def __init__(self) -> None:
        self.count = 0
        self._changed = asyncio.Event()

    def add(self) -> None:
        self.count += 1
        self._changed.set()

    async def until(self, count: int) -> None:
        async def wait() -> None:
            while self.count < count:
                self._changed.clear()
                await self._changed.wait()

        await asyncio.wait_for(wait(), timeout=5)


@pytest.fixture
def polls(monkeypatch: pytest.MonkeyPatch) -> Polls:
    counter = Polls()
    locked = CacheLock.locked

    async def counting(self: CacheLock) -> bool:
        counter.add()
        return await locked(self)

    monkeypatch.setattr(CacheLock, "locked", counting)
    monkeypatch.setattr(cache_module, "_LOCK_POLL_MIN", 0.001)
    monkeypatch.setattr(cache_module, "_LOCK_POLL_MAX", 0.001)
    return counter


@pytest.fixture
def handler() -> Handler:
    return Handler()


@pytest.fixture
async def client(handler: Handler) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(handler) as client:
        yield client


def _client(handler: Handler, **cache_kwargs: Any) -> httpx.AsyncClient:
    app = FastAPI()
    options: dict[str, Any] = {
        "ttl": 60,
        "coalesce": "distributed",
        "debug_header": True,
        **cache_kwargs,
    }

    @app.api_route("/items", methods=["GET", "HEAD"])
    @cache(**options)
    async def items(request: Request) -> dict[str, int]:
        await handler.run()
        return {"n": len(handler.locked)}

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )


async def _get(client: httpx.AsyncClient, method: str = "GET") -> httpx.Response:
    return await asyncio.wait_for(client.request(method, "/items"), timeout=5)


async def test_a_miss_holds_the_lock_while_the_handler_runs(
    client: httpx.AsyncClient, handler: Handler
) -> None:
    assert (await _get(client)).headers["x-cache"] == "MISS"

    assert handler.locked == [True]
    assert not await _locked()
    # A hit takes no lock: nothing renders.
    assert (await _get(client)).headers["x-cache"] == "HIT"
    assert await BackendProxy.get().get_all_keys() == [KEY]


async def test_the_lock_is_released_when_the_handler_raises(
    client: httpx.AsyncClient, handler: Handler
) -> None:
    handler.fail = True

    assert (await _get(client)).status_code == 500

    assert handler.locked == [True]
    assert not await _locked()
    assert _IN_FLIGHT == {}


async def test_coalesce_true_takes_no_lock(handler: Handler) -> None:
    async with _client(handler, coalesce=True) as client:
        await _get(client)

    assert handler.locked == [False]


async def test_a_miss_waits_for_another_worker_and_is_served_its_entry(
    client: httpx.AsyncClient, handler: Handler, polls: Polls
) -> None:
    other = _other_worker()
    assert await other.acquire(blocking=False)
    request = asyncio.ensure_future(_get(client))
    await polls.until(3)
    assert handler.locked == []

    await BackendProxy.get().set(
        KEY,
        CacheEntry(fingerprint='"theirs"', content=b"theirs", stored_at=time.time()),
        ttl=60,
    )
    await other.release()
    response = await request

    assert (response.text, response.headers["x-cache"]) == ("theirs", "HIT")
    assert handler.locked == []


async def test_a_waiter_renders_without_the_lock_when_nothing_was_stored(
    client: httpx.AsyncClient, handler: Handler, polls: Polls
) -> None:
    other = _other_worker()
    assert await other.acquire(blocking=False)
    requests = [asyncio.ensure_future(_get(client)) for _ in range(3)]
    await polls.until(3)

    await other.release()
    responses = await asyncio.gather(*requests)

    # The request leading in this process waited once and then rendered
    # without the lock, so nobody queues behind it in another worker; the
    # two behind it in this process were served what it stored.
    assert handler.locked == [False]
    assert sorted(r.headers["x-cache"] for r in responses) == ["HIT", "HIT", "MISS"]
    assert not await _locked()


async def test_a_waiter_gives_up_on_a_worker_that_never_finishes(
    client: httpx.AsyncClient,
    handler: Handler,
    polls: Polls,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(cache_module, "_LOCK_WAIT_MAX", 0.02)
    other = _other_worker()
    assert await other.acquire(blocking=False)

    response = await _get(client)

    # It rendered while the other worker still held the lock, and left it.
    assert (response.status_code, handler.locked) == (200, [True])
    assert polls.count > 1
    assert await _locked()
    assert "Another worker still renders" in caplog.text
    await other.release()


async def test_head_waits_for_another_worker_but_never_takes_the_lock(
    client: httpx.AsyncClient, handler: Handler, polls: Polls
) -> None:
    assert (await _get(client, "HEAD")).status_code == 200
    assert handler.locked == [False]

    other = _other_worker()
    assert await other.acquire(blocking=False)
    request = asyncio.ensure_future(_get(client, "HEAD"))
    await polls.until(polls.count + 3)
    assert handler.locked == [False]
    await other.release()
    await request

    assert handler.locked == [False, False]


class _NoLocks(MemoryBackend):
    async def set_if_absent(self, *args: Any, **kwargs: Any) -> bool:
        msg = "backend is down"
        raise ConnectionError(msg)


async def test_a_failing_lock_is_a_miss_without_it_when_failing_open(
    handler: Handler,
) -> None:
    BackendProxy.set(_NoLocks())

    async with _client(handler) as client:
        assert (await _get(client)).status_code == 200
    assert handler.locked == [False]

    await BackendProxy.get().clear()
    async with _client(handler, fail_open=False) as client:
        assert (await _get(client)).status_code == 500
    assert handler.locked == [False]


async def test_one_worker_at_a_time_refreshes_a_stale_entry(
    handler: Handler, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    monkeypatch.setattr(stored_response, "_now", lambda: now)
    options = {"stale": "revalidate", "stale_ttl": 30, "serve_stale": True}
    async with _client(handler, **options) as client:
        assert (await _get(client)).json() == {"n": 1}
        now += 61  # past the ttl, inside the stale window

        other = _other_worker()
        assert await other.acquire(blocking=False)
        stale = await _get(client)
        # Another worker is refreshing the key: this one does not.
        assert (stale.json(), stale.headers["x-cache"]) == ({"n": 1}, "STALE")
        assert len(handler.locked) == 1
        await other.release()

        assert (await _get(client)).headers["x-cache"] == "STALE"
        assert handler.locked == [True, True]
        assert not await _locked()
        assert (await _get(client)).json() == {"n": 2}


def test_coalesce_takes_a_bool_or_distributed() -> None:
    with pytest.raises(CacheXError, match="a bool or \"distributed\", got 'workers'"):
        cache(ttl=60, coalesce="workers")(lambda: None)  # type: ignore[arg-type]
    with pytest.raises(CacheXError, match="coalesce needs a positive ttl"):
        cache(coalesce="distributed")(lambda: None)
