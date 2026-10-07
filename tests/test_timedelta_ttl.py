"""A ``ttl`` may be a ``datetime.timedelta`` of whole seconds (#334).

Every place that takes a ``ttl`` (the backends, ``CacheManager``,
``CacheLock``, ``@cache`` and its ``stale_ttl``) converts it to the same
``int`` of seconds it always stored; a fraction of a second is rejected, as a
``float`` is.
"""

import time
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheLock
from fastapi_cachex import CacheManager
from fastapi_cachex import cache
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends.base import MAX_TTL
from fastapi_cachex.backends.base import validate_ttl
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import CacheEntry
from tests.backends.test_base import DictBackend
from tests.live_servers import UNCONNECTED_PORT

ENTRY = CacheEntry(fingerprint="e", content=b"v")
FIVE_MINUTES = timedelta(minutes=5)

BAD_TIMEDELTAS = [
    pytest.param(
        timedelta(seconds=1.5), ValueError, "whole number of seconds", id="fraction"
    ),
    pytest.param(
        timedelta(microseconds=1), ValueError, "whole number of seconds", id="micro"
    ),
    pytest.param(timedelta(0), ValueError, "ttl must be a positive", id="zero"),
    pytest.param(timedelta(seconds=-1), ValueError, "ttl must be a positive", id="neg"),
    pytest.param(timedelta(seconds=MAX_TTL + 1), ValueError, "at most", id="large"),
]


def test_validate_ttl_converts_a_timedelta_to_whole_seconds() -> None:
    assert validate_ttl(FIVE_MINUTES) == 300
    assert validate_ttl(timedelta(days=1)) == 86400
    assert validate_ttl(timedelta(seconds=MAX_TTL)) == MAX_TTL


@pytest.mark.parametrize(("ttl", "error", "match"), BAD_TIMEDELTAS)
def test_validate_ttl_rejects_a_timedelta_outside_the_rules(
    ttl: timedelta, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        validate_ttl(ttl)


async def test_memory_backend_stores_the_seconds_of_a_timedelta(
    memory_backend: MemoryBackend,
) -> None:
    await memory_backend.set("set", ENTRY, ttl=FIVE_MINUTES)
    assert await memory_backend.set_if_absent("absent", ENTRY, ttl=FIVE_MINUTES)
    assert await memory_backend.increment("count", ttl=FIVE_MINUTES) == 1
    await memory_backend.set("equals", ENTRY)
    assert await memory_backend.set_if_equals("equals", ENTRY, ENTRY, ttl=FIVE_MINUTES)
    await memory_backend.set("expire", ENTRY)
    assert await memory_backend.expire_if_equals("expire", ENTRY, ttl=FIVE_MINUTES)

    now = time.time()
    for key in ("set", "absent", "count", "equals", "expire"):
        expiry = memory_backend.cache[key].expiry
        assert expiry is not None
        assert now + 299 < expiry <= now + 301


async def test_memcached_backend_sends_the_seconds_of_a_timedelta() -> None:
    backend = MemcachedBackend([f"127.0.0.1:{UNCONNECTED_PORT}"])
    backend.client = MagicMock()
    backend.client.add.return_value = True

    await backend.set("k", ENTRY, ttl=FIVE_MINUTES)
    assert await backend.set_if_absent("a", ENTRY, ttl=FIVE_MINUTES)

    assert backend.client.set.call_args.args[2] == 300
    assert backend.client.add.call_args.args[2] == 300


async def test_the_base_class_fallbacks_hand_the_backend_whole_seconds() -> None:
    backend = DictBackend()
    assert await backend.set_if_absent("absent", ENTRY, ttl=FIVE_MINUTES)
    assert await backend.increment("count", ttl=FIVE_MINUTES) == 1
    assert await backend.set_if_equals("absent", ENTRY, ENTRY, ttl=FIVE_MINUTES)
    assert await backend.expire_if_equals("absent", ENTRY, ttl=timedelta(minutes=1))

    assert backend.store["count"][1] == 300
    assert backend.store["absent"][1] == 60


@pytest.mark.parametrize(("ttl", "error", "match"), BAD_TIMEDELTAS)
async def test_backends_reject_a_bad_timedelta_before_io(
    memory_backend: MemoryBackend, ttl: timedelta, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        await memory_backend.set("k", ENTRY, ttl=ttl)
    with pytest.raises(error, match=match):
        await memory_backend.increment("n", ttl=ttl)
    assert memory_backend.cache == {}


async def test_cache_manager_accepts_timedeltas(memory_backend: MemoryBackend) -> None:
    manager = CacheManager(
        backend=memory_backend,
        default_ttl=FIVE_MINUTES,
        lock_ttl=timedelta(seconds=30),
    )
    assert manager.default_ttl == 300
    assert manager.lock_ttl == 30

    await manager.set("a", 1)
    await manager.set("b", 2, ttl=timedelta(minutes=1))
    assert await manager.add("c", 3, ttl=timedelta(minutes=2))
    assert (
        await manager.get_or_set(
            "d", lambda: 4, ttl=timedelta(minutes=3), lock_ttl=timedelta(seconds=5)
        )
        == 4
    )

    now = time.time()
    for key, seconds in (("a", 300), ("b", 60), ("c", 120), ("d", 180)):
        expiry = memory_backend.cache[f"cache:{key}"].expiry
        assert expiry is not None
        assert now + seconds - 1 < expiry <= now + seconds + 1


@pytest.mark.parametrize(("ttl", "error", "match"), BAD_TIMEDELTAS)
async def test_cache_manager_rejects_a_bad_timedelta(
    memory_backend: MemoryBackend, ttl: timedelta, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        CacheManager(backend=memory_backend, default_ttl=ttl)
    with pytest.raises(error, match=match):
        CacheManager(backend=memory_backend, lock_ttl=ttl)
    manager = CacheManager(backend=memory_backend)
    factory = MagicMock(return_value=1)
    with pytest.raises(error, match=match):
        await manager.set("k", 1, ttl=ttl)
    with pytest.raises(error, match=match):
        await manager.get_or_set("k", factory, lock_ttl=ttl)
    factory.assert_not_called()


async def test_cache_lock_accepts_timedeltas(memory_backend: MemoryBackend) -> None:
    lock = CacheLock("job", ttl=timedelta(seconds=30), backend=memory_backend)
    assert lock.ttl == 30

    assert await lock.acquire(blocking=False, ttl=timedelta(minutes=1))
    expiry = memory_backend.cache["lock:job"].expiry
    assert expiry is not None
    assert time.time() + 59 < expiry <= time.time() + 61

    assert await lock.extend(ttl=timedelta(minutes=2))
    expiry = memory_backend.cache["lock:job"].expiry
    assert expiry is not None
    assert time.time() + 119 < expiry <= time.time() + 121
    assert await lock.release()


@pytest.mark.parametrize(
    ("ttl", "error", "match"),
    [
        *BAD_TIMEDELTAS,
        pytest.param(0, ValueError, "ttl must be a positive", id="int-zero"),
        pytest.param(1.5, TypeError, "got float", id="float"),
    ],
)
async def test_cache_lock_rejects_a_bad_ttl_when_built(
    memory_backend: MemoryBackend, ttl: Any, error: type[Exception], match: str
) -> None:
    """Before 0.4.2 a bad ``ttl`` surfaced only from ``acquire()``."""
    with pytest.raises(error, match=match):
        CacheLock("job", ttl=ttl, backend=memory_backend)
    lock = CacheLock("job", backend=memory_backend)
    with pytest.raises(error, match=match):
        await lock.acquire(blocking=False, ttl=ttl)
    assert lock._is_held is False
    assert await lock.acquire(blocking=False)
    with pytest.raises(error, match=match):
        await lock.extend(ttl=ttl)
    assert await lock.release()


def test_cache_decorator_accepts_timedeltas(memory_backend: MemoryBackend) -> None:
    BackendProxy.set(memory_backend)
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/items")
    @cache(ttl=FIVE_MINUTES, stale="revalidate", stale_ttl=timedelta(minutes=1))
    async def items() -> dict[str, int]:
        calls["n"] += 1
        return {"calls": calls["n"]}

    client = TestClient(app)
    first = client.get("/items")
    assert first.headers["Cache-Control"] == "max-age=300, stale-while-revalidate=60"
    assert client.get("/items").json() == first.json()
    assert calls["n"] == 1
    expiry = memory_backend.cache["http:v2|GET|testserver|/items|"].expiry
    assert expiry is not None
    assert time.time() + 299 < expiry <= time.time() + 301


def test_cache_decorator_sends_max_age_0_for_a_zero_timedelta() -> None:
    app = FastAPI()

    @app.get("/items")
    @cache(ttl=timedelta(0))
    async def items() -> dict[str, int]:
        return {"n": 1}

    assert TestClient(app).get("/items").headers["Cache-Control"] == "max-age=0"


@pytest.mark.parametrize(
    ("ttl", "match"),
    [
        pytest.param(
            timedelta(seconds=1.5), "must be a whole number of seconds", id="fraction"
        ),
        pytest.param(timedelta(seconds=-1), "must not be negative", id="negative"),
        pytest.param(timedelta(seconds=MAX_TTL + 1), "must be at most", id="too-large"),
    ],
)
def test_cache_decorator_rejects_a_bad_timedelta(ttl: timedelta, match: str) -> None:
    with pytest.raises(CacheXError, match=f"^ttl {match}"):
        cache(ttl=ttl)
    with pytest.raises(CacheXError, match=f"^stale_ttl {match}"):
        cache(ttl=60, stale="error", stale_ttl=ttl)


@pytest.mark.parametrize(
    ("stale_ttl", "match"),
    [
        pytest.param(1.5, "must be an int .* got float", id="float"),
        pytest.param(True, "must be an int .* got bool", id="bool"),
        pytest.param(-1, "must not be negative", id="negative"),
        pytest.param(MAX_TTL + 1, "must be at most", id="too-large"),
    ],
)
def test_stale_ttl_is_checked_like_ttl(stale_ttl: Any, match: str) -> None:
    """A float used to be written into the header as `stale-if-error=1.5`."""
    with pytest.raises(CacheXError, match=f"^stale_ttl {match}"):
        cache(ttl=60, stale="error", stale_ttl=stale_ttl)
