"""`ttl` means the same thing on every backend (#102).

Memcached read an exptime of 0 as "never expire", Redis rejected `EX 0`, and
the memory backend expired the entry at once. Now `None` is the only way to
say "no expiry" and zero or negative values raise `ValueError` before any
backend I/O.

Non-integer TTLs were next (#229): `True` passed as one second, a float worked
on the memory backend only, and on Redis `increment(ttl=1.5)` created the
counter before `EXPIRE` failed, leaving it with no expiry at all. Only an `int`
up to `MAX_TTL` is accepted now, and `TypeError`/`ValueError` still come
before any I/O.
"""

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends.base import MAX_TTL
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.base import validate_ttl
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.manager import CacheManager
from fastapi_cachex.state import StateManager
from fastapi_cachex.types import CacheEntry
from tests.backends.test_base import DictBackend
from tests.live_servers import UNCONNECTED_PORT

ENTRY = CacheEntry(fingerprint="e", content=b"v")
# (ttl, exception, message) for every value validate_ttl must refuse.
BAD_TTLS = [
    pytest.param(0, ValueError, "ttl must be a positive", id="zero"),
    pytest.param(-1, ValueError, "ttl must be a positive", id="negative"),
    pytest.param(1.5, TypeError, "got float", id="float"),
    pytest.param(60.0, TypeError, "got float", id="integral-float"),
    pytest.param(True, TypeError, "got bool", id="bool"),
    pytest.param("60", TypeError, "got str", id="str"),
    pytest.param(MAX_TTL + 1, ValueError, "at most", id="too-large"),
    pytest.param(10**400, ValueError, "at most", id="huge"),
]


def make_backends() -> list[BaseCacheBackend]:
    # None of these touch a server before validating: Redis points at a port
    # nothing listens on and Memcached's client is a stub, so reaching I/O
    # would fail with a different error (or record a call).
    memcached = MemcachedBackend([f"127.0.0.1:{UNCONNECTED_PORT}"])
    memcached.client = MagicMock()
    return [
        MemoryBackend(),
        AsyncRedisCacheBackend(host="127.0.0.1", port=UNCONNECTED_PORT),
        memcached,
        DictBackend(),
    ]


OPERATIONS: dict[str, Callable[[BaseCacheBackend, int], Awaitable[object]]] = {
    "set": lambda backend, ttl: backend.set("k", ENTRY, ttl=ttl),
    "set_if_absent": lambda backend, ttl: backend.set_if_absent("k", ENTRY, ttl=ttl),
    "increment": lambda backend, ttl: backend.increment("n", ttl=ttl),
}


@pytest.mark.parametrize(("ttl", "error", "match"), BAD_TTLS)
@pytest.mark.parametrize("operation", ["set", "set_if_absent", "increment"])
@pytest.mark.asyncio
async def test_backends_reject_invalid_ttl(
    operation: str, ttl: object, error: type[Exception], match: str
) -> None:
    for backend in make_backends():
        if isinstance(backend, DictBackend) and operation == "set":
            continue  # a third-party set() is its own; the fallbacks are ours
        with pytest.raises(error, match=match):
            await OPERATIONS[operation](backend, ttl)  # type: ignore[arg-type]
        if isinstance(backend, MemcachedBackend):
            assert isinstance(backend.client, MagicMock)
            assert backend.client.method_calls == []
        if isinstance(backend, MemoryBackend):
            assert backend.cache == {}
            backend.stop_cleanup()


@pytest.mark.parametrize("ttl", [None, 1, 3600, MAX_TTL])
def test_validate_ttl_passes_none_and_positive(ttl: int | None) -> None:
    assert validate_ttl(ttl) == ttl


@pytest.mark.parametrize(("ttl", "error", "match"), BAD_TTLS)
@pytest.mark.asyncio
async def test_cache_manager_rejects_invalid_ttl(
    ttl: Any, error: type[Exception], match: str
) -> None:
    backend = DictBackend()
    with pytest.raises(error, match=match):
        CacheManager(backend, default_ttl=ttl)

    manager = CacheManager(backend)
    factory = MagicMock(return_value=1)
    with pytest.raises(error, match=match):
        await manager.set("k", 1, ttl=ttl)
    with pytest.raises(error, match=match):
        await manager.add("k", 1, ttl=ttl)
    with pytest.raises(error, match=match):
        await manager.get_or_set("k", factory, ttl=ttl)
    # The ttl is checked before the factory runs.
    factory.assert_not_called()
    assert backend.store == {}


@pytest.mark.parametrize(("ttl", "error", "match"), BAD_TTLS)
@pytest.mark.asyncio
async def test_state_manager_rejects_invalid_ttl(
    ttl: Any, error: type[Exception], match: str
) -> None:
    backend = DictBackend()
    with pytest.raises(error, match=match):
        StateManager(backend, default_ttl=ttl)

    manager = StateManager(backend)
    with pytest.raises(error, match=match):
        await manager.create_state(ttl=ttl)
    assert backend.store == {}


@pytest.mark.parametrize(
    ("delta", "error", "match"),
    [
        pytest.param(1.5, TypeError, "delta must be an int", id="float"),
        pytest.param(True, TypeError, "delta must be an int", id="bool"),
        pytest.param(2**63, ValueError, "signed 64-bit", id="too-large"),
        pytest.param(-(2**63) - 1, ValueError, "signed 64-bit", id="too-small"),
    ],
)
@pytest.mark.asyncio
async def test_backends_reject_invalid_delta(
    delta: Any, error: type[Exception], match: str
) -> None:
    """Such a delta reached the server and was reported as "not a counter"."""
    for backend in make_backends():
        with pytest.raises(error, match=match):
            await backend.increment("n", delta=delta)
        if isinstance(backend, MemcachedBackend):
            assert isinstance(backend.client, MagicMock)
            assert backend.client.method_calls == []
        if isinstance(backend, MemoryBackend):
            assert backend.cache == {}
            backend.stop_cleanup()
