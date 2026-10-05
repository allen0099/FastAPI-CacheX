"""``@cache(coalesce=True)`` runs the handler once for concurrent misses (#252)."""

import asyncio
import importlib
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response

from fastapi_cachex._coalesce import _IN_FLIGHT
from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.proxy import BackendProxy

try:  # Starlette's TestClient moved to httpx2; the `lowest` env still has httpx.
    import httpx2 as httpx  # type: ignore[import-not-found, unused-ignore]
except ImportError:  # pragma: no cover - depends on the installed starlette
    import httpx  # type: ignore[no-redef, import-not-found, unused-ignore]

# `fastapi_cachex.cache` is the decorator once the package is imported.
cache_module = importlib.import_module("fastapi_cachex.cache")

KEY = "http:v2|GET|testserver|/items|"
FOLLOWERS = 4


async def _settle(*requests: "asyncio.Future[httpx.Response]") -> list[httpx.Response]:
    """Wait for ``requests``; fail instead of hanging on one never released."""
    return list(await asyncio.wait_for(asyncio.gather(*requests), timeout=5))


class Waiters:
    """Counts requests that started waiting for a leader.

    Also the test's progress signal: the handler and the counter call
    ``notify()`` on every change, and ``until()`` waits for a condition on
    them, failing instead of hanging when it never holds.
    """

    def __init__(self) -> None:
        self.count = 0
        self._changed = asyncio.Event()

    def notify(self) -> None:
        self._changed.set()

    async def until(self, condition: Callable[[], bool]) -> None:
        async def wait() -> None:
            while not condition():
                self._changed.clear()
                await self._changed.wait()

        await asyncio.wait_for(wait(), timeout=5)


class Handler:
    """A handler that blocks until released, counting runs and overlap."""

    def __init__(self, waiters: Waiters) -> None:
        self.waiters = waiters
        self.release = asyncio.Event()
        self.calls: list[str] = []
        self.running = 0
        self.max_running = 0

    async def run(self, method: str) -> None:
        self.calls.append(method)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self.waiters.notify()
        try:
            # Read at call time, so a test can swap in a new event for the
            # requests that come later.
            await self.release.wait()
        finally:
            self.running -= 1
            self.waiters.notify()


@pytest.fixture
def waiters(monkeypatch: pytest.MonkeyPatch) -> Waiters:
    counter = Waiters()
    wait_for = cache_module._wait_for

    async def counting(leader: "asyncio.Future[None]") -> None:
        counter.count += 1
        counter.notify()
        await wait_for(leader)

    monkeypatch.setattr(cache_module, "_wait_for", counting)
    return counter


@pytest.fixture
async def client_for() -> AsyncIterator[Callable[[FastAPI], httpx.AsyncClient]]:
    clients: list[httpx.AsyncClient] = []

    def make(app: FastAPI) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


def _app(handler: Handler, **cache_kwargs: Any) -> FastAPI:
    app = FastAPI()
    options: dict[str, Any] = {"ttl": 60, "coalesce": True, **cache_kwargs}

    @app.api_route("/items", methods=["GET", "HEAD"])
    @cache(**options)
    async def items(request: Request, response: Response) -> dict[str, int]:
        await handler.run(request.method)
        if request.headers.get("x-cookie"):
            response.set_cookie("seen", "1")
        if request.headers.get("x-fail"):
            msg = "handler failed"
            raise RuntimeError(msg)
        return {"n": 1}

    return app


async def _leader_then_followers(
    client: httpx.AsyncClient,
    handler: Handler,
    waiters: Waiters,
    *,
    leader_headers: dict[str, str] | None = None,
    follower_method: str = "GET",
    follower_headers: dict[str, str] | None = None,
) -> tuple["asyncio.Task[httpx.Response]", list[httpx.Response]]:
    """Start a leader, let FOLLOWERS requests queue behind it, then release."""
    leader = asyncio.ensure_future(client.get("/items", headers=leader_headers))
    await waiters.until(lambda: bool(handler.calls))
    followers = [
        asyncio.ensure_future(
            client.request(follower_method, "/items", headers=follower_headers)
        )
        for _ in range(FOLLOWERS)
    ]
    await waiters.until(lambda: waiters.count == FOLLOWERS)
    handler.release.set()
    return leader, await _settle(*followers)


async def test_concurrent_misses_run_the_handler_once(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler))

    leader, followers = await _leader_then_followers(client, handler, waiters)

    first = await leader
    assert handler.calls == ["GET"]
    assert {r.status_code for r in followers} == {200}
    assert {r.headers["etag"] for r in followers} == {first.headers["etag"]}
    assert all("age" in r.headers for r in followers)
    assert await BackendProxy.get().get_all_keys() == [KEY]
    assert _IN_FLIGHT == {}


async def test_followers_render_at_once_when_nothing_was_stored(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler))
    cookie = {"X-Cookie": "1"}
    leader = asyncio.ensure_future(client.get("/items", headers=cookie))
    await waiters.until(lambda: bool(handler.calls))
    followers = [
        asyncio.ensure_future(client.get("/items", headers=cookie))
        for _ in range(FOLLOWERS)
    ]
    await waiters.until(lambda: waiters.count == FOLLOWERS)

    first_release, handler.release = handler.release, asyncio.Event()
    first_release.set()

    assert "set-cookie" in (await leader).headers
    # Nothing was stored, so every follower runs the handler, all at once
    # rather than one after another.
    await waiters.until(lambda: handler.running == FOLLOWERS)
    handler.release.set()
    assert {r.status_code for r in await _settle(*followers)} == {200}
    assert len(handler.calls) == 1 + FOLLOWERS


async def test_followers_render_when_the_leader_fails(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler))
    leader = asyncio.ensure_future(client.get("/items", headers={"X-Fail": "1"}))
    await waiters.until(lambda: bool(handler.calls))
    followers = [asyncio.ensure_future(client.get("/items")) for _ in range(FOLLOWERS)]
    await waiters.until(lambda: waiters.count == FOLLOWERS)

    handler.release.set()

    with pytest.raises(RuntimeError, match="handler failed"):
        await leader
    assert {r.status_code for r in await _settle(*followers)} == {200}
    # Not 1 + FOLLOWERS: the first follower to render may store its response
    # before the others read again.
    assert len(handler.calls) >= 2
    assert _IN_FLIGHT == {}


async def test_a_cancelled_leader_releases_its_followers(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler))
    leader = asyncio.ensure_future(client.get("/items"))
    await waiters.until(lambda: bool(handler.calls))
    follower = asyncio.ensure_future(client.get("/items"))
    await waiters.until(lambda: waiters.count == 1)

    leader.cancel()
    await waiters.until(lambda: len(handler.calls) == 2)
    handler.release.set()

    assert [r.status_code for r in await _settle(follower)] == [200]
    assert _IN_FLIGHT == {}


async def test_a_cancelled_follower_leaves_the_others_waiting(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler))
    leader = asyncio.ensure_future(client.get("/items"))
    await waiters.until(lambda: bool(handler.calls))
    followers = [asyncio.ensure_future(client.get("/items")) for _ in range(FOLLOWERS)]
    await waiters.until(lambda: waiters.count == FOLLOWERS)

    followers[0].cancel()
    await asyncio.sleep(0)
    handler.release.set()

    assert [r.status_code for r in await _settle(leader, *followers[1:])] == [200] * (
        FOLLOWERS
    )
    assert handler.calls == ["GET"]


async def test_a_follower_gets_a_304_for_the_stored_etag(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    handler.release.set()
    client = client_for(_app(handler))
    etag = (await client.get("/items")).headers["etag"]
    await BackendProxy.get().clear()
    handler.release.clear()
    handler.calls.clear()

    leader, followers = await _leader_then_followers(
        client, handler, waiters, follower_headers={"If-None-Match": etag}
    )

    await leader
    assert handler.calls == ["GET"]
    assert {r.status_code for r in followers} == {304}


async def test_head_waits_for_a_running_get(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler))

    leader, followers = await _leader_then_followers(
        client, handler, waiters, follower_method="HEAD"
    )

    await leader
    assert handler.calls == ["GET"]
    assert {r.status_code for r in followers} == {200}


async def test_head_never_makes_others_wait(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler))
    heads = [asyncio.ensure_future(client.head("/items")) for _ in range(3)]

    await waiters.until(lambda: handler.running == len(heads))
    handler.release.set()

    assert {r.status_code for r in await _settle(*heads)} == {200}
    assert waiters.count == 0
    assert _IN_FLIGHT == {}


async def test_different_keys_do_not_wait_on_each_other(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler))
    requests = [
        asyncio.ensure_future(client.get("/items", params={"page": page}))
        for page in range(3)
    ]

    await waiters.until(lambda: handler.running == len(requests))
    handler.release.set()

    assert {r.status_code for r in await _settle(*requests)} == {200}
    assert waiters.count == 0


async def test_a_failed_backend_read_does_not_wait(
    client_for: Callable[[FastAPI], httpx.AsyncClient],
    waiters: Waiters,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unreachable(key: str) -> None:
        msg = "backend down"
        raise ConnectionError(msg)

    monkeypatch.setattr(BackendProxy.get(), "get", unreachable)
    handler = Handler(waiters)
    client = client_for(_app(handler))
    requests = [asyncio.ensure_future(client.get("/items")) for _ in range(3)]

    await waiters.until(lambda: handler.running == len(requests))
    handler.release.set()

    assert {r.status_code for r in await _settle(*requests)} == {200}
    assert waiters.count == 0
    assert _IN_FLIGHT == {}


async def test_without_coalesce_every_miss_runs_the_handler(
    client_for: Callable[[FastAPI], httpx.AsyncClient], waiters: Waiters
) -> None:
    handler = Handler(waiters)
    client = client_for(_app(handler, coalesce=False))
    requests = [asyncio.ensure_future(client.get("/items")) for _ in range(3)]

    await waiters.until(lambda: handler.running == len(requests))
    handler.release.set()

    assert {r.status_code for r in await _settle(*requests)} == {200}
    assert waiters.count == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"ttl": None}, id="no-ttl"),
        pytest.param({"ttl": 0}, id="ttl-0"),
        pytest.param({"ttl": 60, "private": True}, id="private"),
        pytest.param({"ttl": 60, "no_cache": True}, id="no-cache"),
    ],
)
def test_coalesce_is_rejected_where_nothing_stored_is_served(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(CacheXError, match="coalesce needs a positive ttl"):
        cache(coalesce=True, **kwargs)(lambda: None)


@pytest.mark.parametrize("value", [1, "yes", None])
def test_coalesce_must_be_a_bool(value: object) -> None:
    with pytest.raises(CacheXError, match="coalesce must be a bool"):
        cache(ttl=60, coalesce=value)(lambda: None)  # type: ignore[arg-type]


def test_no_store_warns_that_it_overrides_coalesce() -> None:
    with pytest.warns(UserWarning, match="coalesce"):
        cache(no_store=True, coalesce=True)(lambda: None)
