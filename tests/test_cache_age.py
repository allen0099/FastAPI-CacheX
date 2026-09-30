"""The ``Age`` header on responses served from a stored entry (#254).

Without it a downstream cache reading ``max-age=<ttl>`` on a hit restarts the
freshness clock, so a response could be reused for up to twice the ttl. With
``Age`` it computes ``max-age - Age`` (RFC 9111 §4.2.3).

Time is moved by patching ``fastapi_cachex.cache._now``; the memory backend
keeps real time, so entries do not expire while the patched clock runs ahead.
"""

import importlib
import json
import math
import uuid
from collections.abc import AsyncIterator
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi import Response

from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.backends import codec
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.backends.codec import encode_entry
from fastapi_cachex.cache import cache
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import requires_memcached
from tests.live_servers import requires_redis

try:  # Starlette's TestClient moved to httpx2; the `lowest` env still has httpx.
    import httpx2 as httpx  # type: ignore[import-not-found, unused-ignore]
except ImportError:  # pragma: no cover - depends on the environment
    import httpx  # type: ignore[no-redef, import-not-found, unused-ignore]

# `fastapi_cachex.cache` is shadowed by the `cache` decorator on the package.
cache_module = importlib.import_module("fastapi_cachex.cache")

TTL = 60
START = 1_800_000_000.0
KEY = "http:v2|GET|testserver|/item|"


class _Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(cache_module, "_now", clock)
    return clock


def _app(**cache_kwargs: object) -> tuple[FastAPI, dict[str, int]]:
    calls = {"count": 0}
    app = FastAPI()
    kwargs: dict[str, object] = {"ttl": TTL, **cache_kwargs}

    @app.api_route("/item", methods=["GET", "POST"])
    @cache(**kwargs)  # type: ignore[arg-type]
    async def item() -> dict[str, int]:
        calls["count"] += 1
        return {"value": 1}

    return app, calls


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


async def test_miss_sends_no_age_and_stores_stored_at(clock, client_for):
    app, _ = _app()
    client = client_for(app)

    response = await client.get("/item")

    assert response.status_code == 200
    assert "age" not in response.headers
    entry = await BackendProxy.get().get(KEY)
    assert entry is not None
    assert entry.stored_at == START


@pytest.mark.parametrize("elapsed", [0, 1, 7, 59.9])
async def test_hit_at_t_plus_n_sends_age_n(clock, client_for, elapsed):
    app, calls = _app()
    client = client_for(app)
    await client.get("/item")

    clock.now = START + elapsed
    response = await client.get("/item")

    assert calls["count"] == 1
    assert response.headers["age"] == str(int(elapsed))
    # max-age stays the ttl: downstream subtracts Age itself.
    assert response.headers["cache-control"] == f"max-age={TTL}"
    assert len(response.headers.get_list("age")) == 1


async def test_age_beyond_ttl_is_clamped_to_ttl(clock, client_for):
    app, _ = _app()
    client = client_for(app)
    await client.get("/item")

    clock.now = START + TTL * 10
    response = await client.get("/item")

    assert response.headers["age"] == str(TTL)


async def test_stored_at_in_the_future_gives_age_zero(clock, client_for):
    """Another host's clock running ahead must not produce a negative Age."""
    app, _ = _app()
    client = client_for(app)
    await client.get("/item")

    clock.now = START - 30
    response = await client.get("/item")

    assert response.headers["age"] == "0"


async def test_304_from_the_cached_etag_carries_age(clock, client_for):
    app, calls = _app()
    client = client_for(app)
    etag = (await client.get("/item")).headers["etag"]

    clock.now = START + 12
    response = await client.get("/item", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert calls["count"] == 1
    assert response.headers["age"] == "12"
    assert response.headers["cache-control"] == f"max-age={TTL}"


async def test_no_cache_revalidation_sends_no_age(clock, client_for):
    """``no_cache`` renders afresh on every request, 304 or not."""
    app, calls = _app(no_cache=True)
    client = client_for(app)
    etag = (await client.get("/item")).headers["etag"]

    clock.now = START + 12
    not_modified = await client.get("/item", headers={"If-None-Match": etag})
    full = await client.get("/item")

    assert calls["count"] == 3
    assert not_modified.status_code == 304
    assert "age" not in not_modified.headers
    assert full.status_code == 200
    assert "age" not in full.headers


@pytest.mark.parametrize(
    ("cache_kwargs", "method", "headers"),
    [
        ({"no_store": True}, "GET", {}),
        ({"private": True}, "GET", {}),
        ({"ttl": 0}, "GET", {}),
        ({}, "POST", {}),
        ({}, "GET", {"Authorization": "Bearer t"}),
    ],
    ids=["no-store", "private", "ttl-0", "non-get", "credential"],
)
async def test_bypass_paths_send_no_age(
    clock, client_for, cache_kwargs, method, headers
):
    app, calls = _app(**cache_kwargs)
    client = client_for(app)
    await client.request(method, "/item", headers=headers)
    # Something stored under the key must not leak into a bypassed answer.
    await BackendProxy.get().set(
        KEY, CacheEntry(fingerprint='W/"x"', content=b"{}", stored_at=START), ttl=TTL
    )

    clock.now = START + 5
    response = await client.request(method, "/item", headers=headers)

    assert calls["count"] == 2
    assert "age" not in response.headers


async def test_legacy_entry_without_stored_at_sends_no_age(clock, client_for):
    app, calls = _app()
    client = client_for(app)
    legacy = CacheEntry(fingerprint='W/"legacy"', content=b'{"value": 0}')
    await BackendProxy.get().set(KEY, legacy, ttl=TTL)

    hit = await client.get("/item")
    not_modified = await client.get("/item", headers={"If-None-Match": 'W/"legacy"'})

    assert calls["count"] == 0
    assert hit.json() == {"value": 0}
    assert "age" not in hit.headers
    assert not_modified.status_code == 304
    assert "age" not in not_modified.headers


async def test_handler_age_header_is_not_replayed(clock, client_for):
    """A handler's own Age describes its response, not the stored copy."""
    app = FastAPI()

    @app.get("/item")
    @cache(ttl=TTL)
    async def item() -> Response:
        return Response(b"x", headers={"Age": "999"})

    client = client_for(app)
    await client.get("/item")
    entry = await BackendProxy.get().get(KEY)
    assert entry is not None
    assert entry.headers == ()

    clock.now = START + 3
    response = await client.get("/item")

    assert response.headers.get_list("age") == ["3"]


def test_age_headers_without_a_ttl_is_not_clamped(clock):
    entry = CacheEntry(fingerprint="f", content=b"", stored_at=START)
    clock.now = START + 10_000.5

    assert cache_module._age_headers(entry, None) == {"age": "10000"}
    assert cache_module._age_headers(entry, 60) == {"age": "60"}


# --- codec -------------------------------------------------------------------


def test_codec_round_trips_stored_at():
    entry = CacheEntry(fingerprint="f", content=b"body", stored_at=START + 0.25)

    assert decode_entry(encode_entry(entry)) == entry


def test_codec_decodes_a_document_without_stored_at_as_none():
    legacy = json.dumps(
        {
            "fingerprint": 'W/"abc"',
            "content": "hello",
            "media_type": "text/plain",
            "status_code": 200,
            "headers": None,
        }
    )

    entry = decode_entry(legacy)

    assert entry is not None
    assert entry.stored_at is None
    assert entry.content == b"hello"


@pytest.mark.parametrize("value", ["1800000000", True, None, [1]])
def test_codec_reads_a_malformed_stored_at_as_unknown(value):
    raw = json.dumps({"fingerprint": "f", "content": "", "stored_at": value})

    entry = decode_entry(raw)

    assert entry is not None
    assert entry.stored_at is None


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_codec_reads_a_non_finite_stored_at_as_unknown(value):
    """Only stdlib json parses these; orjson rejects the whole document."""
    assert codec._stored_at(value) is None


def test_codec_reads_an_integer_stored_at():
    raw = json.dumps({"fingerprint": "f", "content": "", "stored_at": 1_800_000_000})

    entry = decode_entry(raw)

    assert entry is not None
    assert entry.stored_at == START


def test_counter_entries_carry_no_stored_at():
    assert counter_entry(3).stored_at is None
    assert encode_entry(counter_entry(3)) == b"3"


# --- every backend -----------------------------------------------------------


async def _check_backend_serves_age(
    backend: BaseCacheBackend, clock: _Clock, client_for
) -> None:
    BackendProxy.set(backend)
    app, calls = _app()
    client = client_for(app)
    try:
        etag = (await client.get("/item")).headers["etag"]
        stored = await backend.get(KEY)
        assert stored is not None
        assert stored.stored_at == START

        clock.now = START + 9
        hit = await client.get("/item")
        not_modified = await client.get("/item", headers={"If-None-Match": etag})

        assert calls["count"] == 1
        assert hit.headers["age"] == "9"
        assert not_modified.status_code == 304
        assert not_modified.headers["age"] == "9"
    finally:
        await backend.delete(KEY)


async def test_memory_backend_serves_age(clock, client_for):
    backend = MemoryBackend()
    try:
        await _check_backend_serves_age(backend, clock, client_for)
    finally:
        await backend.aclose()


@requires_redis
async def test_redis_backend_serves_age(clock, client_for):
    from fastapi_cachex.backends import AsyncRedisCacheBackend

    backend = AsyncRedisCacheBackend(
        host=REDIS_HOST, port=REDIS_PORT, key_prefix=f"cachex_age_{uuid.uuid4().hex}:"
    )
    await _check_backend_serves_age(backend, clock, client_for)


@requires_memcached
async def test_memcached_backend_serves_age(clock, client_for):
    from fastapi_cachex.backends import MemcachedBackend

    backend = MemcachedBackend(
        servers=[MEMCACHED_SERVER], key_prefix=f"cachex_age_{uuid.uuid4().hex}:"
    )
    await _check_backend_serves_age(backend, clock, client_for)
