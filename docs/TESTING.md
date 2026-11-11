# Testing an app that uses FastAPI-CacheX {#testing}

A cache exists to carry state from one request to the next, and without care it carries it from one test to the next as well. Three things outlive a test unless you reset them:

- **The backend registered with `BackendProxy`.** It is process-wide. `@cache` and the `CacheBackend` / `AppCache` dependencies also register an in-process `MemoryBackend` the first time they find none, and that one stays registered too.
- **The `CacheManager` registered with `CacheManagerProxy`.** `AppCache` and `@cached` create one on first use and keep it, along with the backend it was built on.
- **The entries themselves.** A `@cache` route keeps answering from the entry an earlier test stored for the same method, host, path and query.

The symptom is a test that passes alone and fails in a suite, or passes only in one order.

## A fresh cache for every test {#fresh-cache}

Give each test its own `MemoryBackend` and clear both proxies around it. In `conftest.py`:

```python
from collections.abc import Iterator

import pytest

from fastapi_cachex import BackendProxy, CacheManagerProxy
from fastapi_cachex.backends import MemoryBackend


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[MemoryBackend]:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    CacheManagerProxy.set(None)
    yield backend
    BackendProxy.set(None)
    CacheManagerProxy.set(None)
    backend.stop_cleanup()
```

`BackendProxy.set(None)` only unregisters a backend; it does not empty or close it. Here the backend is dropped with the test. `stop_cleanup()` cancels its cleanup task if that task's event loop is still open and, unlike `aclose()`, needs no `await`, so a sync fixture can call it. Clearing `CacheManagerProxy` matters as much: a manager kept from an earlier test still writes to that test's backend. Resetting the proxies does not reach a backend or manager you pass explicitly (`CacheManager(backend=...)`, `@cached(manager=...)`, `CacheLock(backend=...)`); those need their own fixture.

Prove the isolation before relying on it. The first test of a suite should be one that would fail if entries leaked: two apps that answer the same request differently.

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import cache


def make_app(answer: str) -> FastAPI:
    app = FastAPI()

    @app.get("/answer")
    @cache(ttl=60, debug_header=True)
    async def read_answer() -> dict[str, str]:
        return {"answer": answer}

    return app


def test_first_run() -> None:
    client = TestClient(make_app("run-a"))

    assert client.get("/answer").json() == {"answer": "run-a"}
    assert client.get("/answer").headers["X-Cache"] == "HIT"


def test_second_run_does_not_see_the_first() -> None:
    response = TestClient(make_app("run-b")).get("/answer")

    assert response.json() == {"answer": "run-b"}
    assert response.headers["X-Cache"] == "MISS"
```

Without the fixture the second test gets `{"answer": "run-a"}` as a `HIT`: both apps build the key `http:v2|GET|testserver|/answer|`, and the fallback backend from the first test is still registered.

## Turning the cache off {#switching-off}

While developing, a cached route hides the change you just made; and a test
that is not about caching is simpler without one. One switch turns caching
off for the whole process:

```python
from fastapi_cachex import set_enabled

set_enabled(False)  # and set_enabled(True) to turn it back on
```

or, without touching the code, start the process with
`FASTAPI_CACHEX_DISABLED=1` (also `true`, `yes` or `on`; read once, when
`fastapi_cachex` is imported, and logged as a warning). `is_enabled()` tells
which it is.

While it is off:

| | Does |
|---|---|
| `@cache` | Runs the handler on every request and neither reads nor writes the backend. The response has the same `Cache-Control` and `ETag` as with caching on, and a matching `If-None-Match` still gets a 304, so a browser or a CDN in front behaves the same. `X-Cache` is `BYPASS`, with `X-Cache-Reason: disabled`. |
| `@cached`, `get_or_set()` | Run the function or factory on every call: no read, no lock, no write. The value still goes through the JSON round trip (and `type_`), so it has the shape a hit would have. |
| `CacheManager.get()`, `set()`, `add()`, `delete()`, the backends, `CacheLock`, `invalidate()` and the other clearing functions | Work as usual. |

The last row is deliberate. `get()` and `set()` are also used to *store*
things: a one-time code, a counter, a flag another request reads back. If the
switch made `get()` return nothing, turning the cache off would break those
features instead of only making the app slower. The cost is that code written
as `get()`, compute, `set()` keeps returning the stored value with the switch
off; write it as `get_or_set()` and it follows the switch.

In a test suite, turn it off for the tests that are not about caching and
leave it on for those that are:

```python
@pytest.fixture
def no_cache() -> Iterator[None]:
    set_enabled(False)
    yield
    set_enabled(True)
```

Do not set the variable in production: every request then runs its handler.

## Asserting that something was cached {#asserting}

`debug_header=True` makes `@cache` say how each GET or HEAD request was answered, in an `X-Cache` header: `HIT` (served from the stored entry), `MISS` (the handler ran and the backend was consulted) or `BYPASS` (the backend was neither read nor written). That is a steadier thing to assert than a call counter in the handler. Leave it off in production unless you want clients to see it; a test can turn it on through a setting.

To test expiry, do not sleep through a TTL. Remove the entry and assert that the next request is a `MISS`. The backend methods are coroutines, so a sync test runs them with `asyncio.run`:

```python
import asyncio


def test_entry_is_gone_after_clear(fresh_cache: MemoryBackend) -> None:
    client = TestClient(make_app("run-a"))
    client.get("/answer")

    asyncio.run(fresh_cache.clear())

    assert client.get("/answer").headers["X-Cache"] == "MISS"
```

`fresh_cache.clear_path("/answer")` removes one path instead; it leaves entries stored with a query string unless you pass `include_params=True`. Inside the app, a handler can call `await invalidate(request)`.

`app.dependency_overrides[get_cache_backend]` is not a substitute for the fixture: it changes only what the `CacheBackend` dependency returns, while `@cache` keeps reading `BackendProxy`.

## Redis {#redis}

Two more things matter with a network backend.

**Create the backend inside the lifespan.** The Redis client binds its connections to the event loop it first runs on. A backend created at import time and shared by tests that each run their own loop fails with errors about a closed or different loop. Built in the lifespan, it lives exactly as long as the app instance, as in [Closing a backend](BACKENDS.md#closing-a-backend). `TestClient` runs the lifespan only when it is used as a context manager, and `httpx.AsyncClient` with `ASGITransport` does not run it at all.

**Isolate the keys, not just the proxy.** Clearing `BackendProxy` leaves every key in Redis, where the next test (or the next run) finds them. Give each test its own `key_prefix` and clear it on shutdown; `clear()` deletes only the keys under the backend's prefix.

```python
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy, cache
from fastapi_cachex.backends import AsyncRedisCacheBackend


def make_app(answer: str, key_prefix: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        backend = AsyncRedisCacheBackend(
            host="127.0.0.1",
            port=int(os.environ["TEST_REDIS_PORT"]),
            key_prefix=key_prefix,
        )
        BackendProxy.set(backend)
        try:
            yield
        finally:
            BackendProxy.set(None)  # unregister
            try:
                await backend.clear()  # delete this test's keys
            finally:
                await backend.aclose()  # close the connection pool

    app = FastAPI(lifespan=lifespan)

    @app.get("/answer")
    @cache(ttl=60, debug_header=True)
    async def read_answer() -> dict[str, str]:
        return {"answer": answer}

    return app


@pytest.fixture
def key_prefix() -> str:
    return f"test:{uuid.uuid4().hex}:"


@pytest.mark.parametrize("answer", ["run-a", "run-b"])
def test_runs_are_isolated(answer: str, key_prefix: str) -> None:
    with TestClient(make_app(answer, key_prefix)) as client:
        assert client.get("/answer").json() == {"answer": answer}
        assert client.get("/answer").headers["X-Cache"] == "HIT"
```

The three steps of the teardown do three different things: unregistering stops later code from finding the backend, clearing removes what it stored, and closing releases its sockets. Skip the last and the suite ends with `ResourceWarning`s for unclosed connections; the `finally` blocks make sure a failing test or a failing `clear()` does not skip it.

Point such tests at a Redis that exists for them (a container started by the suite or by CI), never at one an application uses. In your own app, take the prefix from a setting so tests can override it. Memcached cannot enumerate keys, so there `clear()` flushes the whole server and says so with a `RuntimeWarning`: give Memcached tests a server of their own, and expect the warning (`pytest.warns`) if your suite turns warnings into errors.

## Checklist {#checklist}

- An autouse fixture sets a fresh `MemoryBackend` and clears `BackendProxy` and `CacheManagerProxy` after each test.
- One test proves isolation: the same request, two apps, two answers.
- Network backends are built in the lifespan, and tests use `with TestClient(app) as client:`.
- Each test has its own `key_prefix`; teardown unregisters, clears and closes, in that order.
- Assertions read `X-Cache` rather than counting handler calls, and nothing sleeps through a TTL.
