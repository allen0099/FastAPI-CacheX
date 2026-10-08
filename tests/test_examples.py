"""Run every app in `examples/` through its main flow.

The examples are documentation, so these tests keep them working: each one is
loaded fresh from its file, started with its lifespan, and driven through
`TestClient`. A `DeprecationWarning` fails the test, so an example cannot keep
showing an API we are phasing out.

`redis_backend` needs the redis extra and is skipped without it (checked with
`find_spec`, never imported here). It also talks to a real server, so it
follows the opt-in rules in `tests/live_servers.py`.
"""

import importlib.util
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.manager_proxy import CacheManagerProxy
from fastapi_cachex.proxy import BackendProxy
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import redis_skip_reason

pytestmark = pytest.mark.filterwarnings("error::DeprecationWarning")

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_PROXIES = (BackendProxy, CacheManagerProxy)


@pytest.fixture(autouse=True)
def _reset_proxies() -> Iterator[None]:
    """Every example registers its own backend and managers at import."""
    for proxy in _PROXIES:
        proxy.set(None)
    yield
    for proxy in _PROXIES:
        proxy.set(None)


def load_example(name: str) -> ModuleType:
    """Import `examples/<name>.py` as a new module, so no state is shared."""
    module_name = f"_cachex_example_{name}"
    spec = importlib.util.spec_from_file_location(
        module_name, EXAMPLES_DIR / f"{name}.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[module_name]
    return module


def test_every_example_has_a_test() -> None:
    """A new example must be added to this file (and to examples/README.md)."""
    tested = {
        "app_cache",
        "cache_lock",
        "http_cache",
        "rate_limit",
        "redis_backend",
    }
    assert {p.stem for p in EXAMPLES_DIR.glob("*.py")} == tested
    readme = (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
    for name in tested:
        assert f"{name}.py" in readme


def test_http_cache() -> None:
    example = load_example("http_cache")
    with TestClient(example.app) as client:
        first = client.get("/products/1")
        second = client.get("/products/1")
        assert first.status_code == second.status_code == 200
        assert (
            first.json()
            == second.json()
            == {
                "id": 1,
                "name": "Keyboard",
                "price": 49,
            }
        )
        # Miss, then hit: the handler ran once.
        assert example.handler_runs["product"] == 1
        assert first.headers["cache-control"] == "max-age=60"

        etag = first.headers["etag"]
        not_modified = client.get("/products/1", headers={"If-None-Match": etag})
        assert not_modified.status_code == 304
        assert example.handler_runs["product"] == 1

        # The update clears the cached copy.
        assert client.put("/products/1", params={"price": 59}).status_code == 200
        assert client.get("/products/1").json()["price"] == 59
        assert example.handler_runs["product"] == 2

        stock = client.get("/stock/1")
        assert stock.headers["cache-control"] == "no-cache"
        prefs = client.get("/me/preferences")
        assert "private" in prefs.headers["cache-control"]


def test_http_cache_monitoring_routes_need_the_admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = load_example("http_cache")
    with TestClient(example.app) as client:
        monkeypatch.delenv("CACHE_ADMIN_TOKEN", raising=False)
        # Closed while no token is configured, whatever the client sends.
        assert client.get("/_cache/cached-hits").status_code == 403
        assert (
            client.get("/_cache/cached-hits", headers={"X-Admin-Token": ""}).status_code
            == 403
        )

        monkeypatch.setenv("CACHE_ADMIN_TOKEN", "test-admin-token")
        client.get("/products/2")
        assert (
            client.get(
                "/_cache/cached-hits", headers={"X-Admin-Token": "wrong"}
            ).status_code
            == 403
        )
        hits = client.get(
            "/_cache/cached-hits", headers={"X-Admin-Token": "test-admin-token"}
        )
        assert hits.status_code == 200
        assert "/products/2" in hits.text


def test_app_cache() -> None:
    example = load_example("app_cache")
    with TestClient(example.app) as client:
        first = client.get("/rates")
        second = client.get("/rates")
        assert first.json() == second.json() == {"EUR": 0.92, "JPY": 151.3}
        # The factory ran once for two reads.
        assert example.upstream_calls["rates"] == 1

        assert client.delete("/rates").json() == {"deleted": True}
        client.get("/rates")
        assert example.upstream_calls["rates"] == 2

        created = client.post("/orders", headers={"Idempotency-Key": "abc"})
        assert created.status_code == 201
        order_id = created.json()["order_id"]
        retry = client.post("/orders", headers={"Idempotency-Key": "abc"})
        assert retry.status_code == 409
        assert order_id in retry.json()["detail"]
        other = client.post("/orders", headers={"Idempotency-Key": "def"})
        assert other.status_code == 201

        calls = example.upstream_calls["rates"]
        assert client.get("/rates/eur").json() == {"eur": 0.92}
        assert client.get("/rates/eur").json() == {"eur": 0.92}
        assert example.upstream_calls["rates"] == calls + 1
        assert client.delete("/rates/eur").json() == {"deleted": True}
        client.get("/rates/eur")
        assert example.upstream_calls["rates"] == calls + 2


def test_cache_lock_serialises_rebuilds() -> None:
    example = load_example("cache_lock")
    with TestClient(example.app) as client, ThreadPoolExecutor(3) as pool:
        results = list(
            pool.map(
                lambda _: client.post("/reports/sales/rebuild").status_code, range(3)
            )
        )
    assert results == [200, 200, 200]
    # Three concurrent requests, never more than one inside the lock.
    assert example.rebuilds["max_running"] == 1


def test_cache_lock_non_blocking_import_refuses_a_second_run() -> None:
    example = load_example("cache_lock")
    with TestClient(example.app) as client, ThreadPoolExecutor(2) as pool:
        statuses = sorted(
            pool.map(lambda _: client.post("/imports").status_code, range(2))
        )
        assert statuses == [200, 409]
        # Released afterwards.
        assert client.post("/imports").status_code == 200


def test_rate_limit() -> None:
    example = load_example("rate_limit")
    with TestClient(example.app) as client:
        for _ in range(example.LIMIT):
            assert client.get("/search", params={"q": "x"}).status_code == 200
        limited = client.get("/search", params={"q": "x"})
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= example.WINDOW


@pytest.mark.skipif(
    importlib.util.find_spec("redis") is None,
    reason="redis_backend needs the redis extra",
)
def test_redis_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    reason = redis_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    monkeypatch.setenv("REDIS_HOST", REDIS_HOST)
    monkeypatch.setenv("REDIS_PORT", str(REDIS_PORT))
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("REDIS_DB", raising=False)
    example = load_example("redis_backend")
    with TestClient(example.app) as client:
        backend = BackendProxy.get()
        client.portal.call(backend.clear)  # type: ignore[union-attr]
        try:
            first = client.get("/hello/redis")
            assert first.json() == {"hello": "redis"}
            etag = first.headers["etag"]
            cached = client.get("/hello/redis", headers={"If-None-Match": etag})
            assert cached.status_code == 304
            assert client.post("/visits").json() == {"visits": 1}
            assert client.post("/visits").json() == {"visits": 2}
        finally:
            client.portal.call(backend.clear)  # type: ignore[union-attr]
    # The lifespan unregistered the backend on shutdown.
    with pytest.raises(BackendNotFoundError):
        BackendProxy.get()
