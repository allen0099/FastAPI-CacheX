"""`@cache` on a `MemoryBackend(max_entries=...)` stays within the limit (#242)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import cache
from fastapi_cachex.backends import MemoryBackend


def test_client_chosen_query_strings_cannot_grow_the_cache_past_the_limit() -> None:
    backend = MemoryBackend(max_entries=3)
    BackendProxy.set(backend)
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/items")
    @cache(ttl=3600)
    async def items() -> dict[str, int]:
        calls["n"] += 1
        return {"calls": calls["n"]}

    client = TestClient(app)
    for junk in range(10):
        assert client.get(f"/items?junk={junk}").status_code == 200

    assert len(backend.cache) == 3
    assert calls["n"] == 10
    # The three most recent variants are served from the cache.
    assert client.get("/items?junk=9").json() == {"calls": 10}
    assert calls["n"] == 10
    # The oldest was evicted and renders again.
    assert client.get("/items?junk=0").json() == {"calls": 11}
    backend.stop_cleanup()
