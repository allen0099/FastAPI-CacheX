"""`ttl` means the same thing on every backend (#102).

Memcached read an exptime of 0 as "never expire", Redis rejected `EX 0`, and
the memory backend expired the entry at once. Now `None` is the only way to
say "no expiry" and zero or negative values raise `ValueError` before any
backend I/O.
"""

from collections.abc import Awaitable
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.base import validate_ttl
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.manager import CacheManager
from fastapi_cachex.state import StateManager
from fastapi_cachex.types import CacheEntry
from tests.backends.test_base import DictBackend
from tests.live_servers import UNCONNECTED_PORT

ENTRY = CacheEntry(fingerprint="e", content=b"v")
BAD_TTLS = [0, -1]


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


@pytest.mark.parametrize("ttl", BAD_TTLS)
@pytest.mark.parametrize("operation", ["set", "set_if_absent", "increment"])
@pytest.mark.asyncio
async def test_backends_reject_non_positive_ttl(operation: str, ttl: int) -> None:
    for backend in make_backends():
        if isinstance(backend, DictBackend) and operation == "set":
            continue  # a third-party set() is its own; the fallbacks are ours
        with pytest.raises(ValueError, match="ttl must be a positive"):
            await OPERATIONS[operation](backend, ttl)
        if isinstance(backend, MemcachedBackend):
            assert isinstance(backend.client, MagicMock)
            assert backend.client.method_calls == []
        if isinstance(backend, MemoryBackend):
            assert backend.cache == {}
            backend.stop_cleanup()


@pytest.mark.parametrize("ttl", [None, 1, 3600])
def test_validate_ttl_passes_none_and_positive(ttl: int | None) -> None:
    assert validate_ttl(ttl) == ttl


@pytest.mark.parametrize("ttl", BAD_TTLS)
@pytest.mark.asyncio
async def test_cache_manager_rejects_non_positive_ttl(ttl: int) -> None:
    backend = DictBackend()
    with pytest.raises(ValueError, match="ttl must be a positive"):
        CacheManager(backend, default_ttl=ttl)

    manager = CacheManager(backend)
    factory = MagicMock(return_value=1)
    with pytest.raises(ValueError, match="ttl must be a positive"):
        await manager.set("k", 1, ttl=ttl)
    with pytest.raises(ValueError, match="ttl must be a positive"):
        await manager.add("k", 1, ttl=ttl)
    with pytest.raises(ValueError, match="ttl must be a positive"):
        await manager.get_or_set("k", factory, ttl=ttl)
    # The ttl is checked before the factory runs.
    factory.assert_not_called()
    assert backend.store == {}


@pytest.mark.parametrize("ttl", BAD_TTLS)
@pytest.mark.asyncio
async def test_state_manager_rejects_non_positive_ttl(ttl: int) -> None:
    backend = DictBackend()
    with pytest.raises(ValueError, match="ttl must be a positive"):
        StateManager(backend, default_ttl=ttl)

    manager = StateManager(backend)
    with pytest.raises(ValueError, match="ttl must be a positive"):
        await manager.create_state(ttl=ttl)
    assert backend.store == {}
