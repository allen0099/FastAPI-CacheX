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

`BackendProxy.set(None)` only unregisters a backend; it does not empty or close it. Here the backend is dropped with the test, and `stop_cleanup()` cancels its background task. Clearing `CacheManagerProxy` matters as much: a manager kept from an earlier test still writes to that test's backend.

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

## Asserting that something was cached {#asserting}

`debug_header=True` makes `@cache` say how each GET or HEAD request was answered, in an `X-Cache` header: `HIT` (served from the stored entry), `MISS` (the handler ran and the backend was consulted) or `BYPASS` (the backend was neither read nor written). That is a steadier thing to assert than a call counter in the handler. Leave it off in production unless you want clients to see it; a test can turn it on through a setting.

To test expiry, do not sleep through a TTL. Remove the entry instead (`await invalidate(request)`, `await backend.clear_path("/answer")`, or `await backend.clear()` on the fixture's backend) and assert that the next request is a `MISS`.

## Redis {#redis}

Two more things matter with a network backend.

**Create the backend inside the lifespan.** The Redis client binds its connections to the event loop it first runs on. A backend created at import time and shared by tests that each run their own loop fails with errors about a closed or different loop. Built in the lifespan, it lives exactly as long as the app instance, as in [Closing a backend](BACKENDS.md#closing-a-backend). `TestClient` runs the lifespan only when it is used as a context manager.

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
        yield
        BackendProxy.set(None)  # unregister
        await backend.clear()  # delete this test's keys
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

The three steps of the teardown do three different things: unregistering stops later code from finding the backend, clearing removes what it stored, and closing releases its sockets. Skip the last and the suite ends with `ResourceWarning`s for unclosed connections.

Point such tests at a Redis that exists for them (a container started by the suite or by CI), never at one an application uses. In your own app, take the prefix from a setting so tests can override it. Memcached cannot enumerate keys, so there `clear()` flushes the whole server: give Memcached tests a server of their own.

## Checklist {#checklist}

- An autouse fixture sets a fresh `MemoryBackend` and clears `BackendProxy` and `CacheManagerProxy` after each test.
- One test proves isolation: the same request, two apps, two answers.
- Network backends are built in the lifespan, and tests use `with TestClient(app) as client:`.
- Each test has its own `key_prefix`; teardown unregisters, clears and closes, in that order.
- Assertions read `X-Cache` rather than counting handler calls, and nothing sleeps through a TTL.
