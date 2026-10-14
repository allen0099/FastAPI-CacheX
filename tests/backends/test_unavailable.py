"""A backend that cannot reach its server raises ``BackendUnavailableError`` (#260).

The error raised is also the client's own exception type and is chained to the
original, so code that caught ``redis.exceptions.ConnectionError`` or
``OSError`` before keeps working. These tests need no server: they point the
backends at a port nothing listens on.
"""

import pickle
import socket

import pytest

from fastapi_cachex import BackendUnavailableError
from fastapi_cachex import CacheManager
from fastapi_cachex import CacheXError
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex.backends._unavailable import as_unavailable
from fastapi_cachex.backends._unavailable import translate_unavailable
from fastapi_cachex.types import CacheEntry
from tests.live_servers import REDIS_HOST
from tests.live_servers import UNCONNECTED_PORT

ENTRY = CacheEntry(fingerprint="f", content=b"x")


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def _assert_unavailable(error: BaseException, original: type[BaseException]) -> None:
    assert isinstance(error, BackendUnavailableError)
    assert isinstance(error, CacheXError)
    assert isinstance(error, original)
    assert type(error).__name__ == "BackendUnavailableError"
    assert isinstance(error.__cause__, original)
    assert not isinstance(error.__cause__, BackendUnavailableError)


async def test_redis_refused_connection() -> None:
    redis_exceptions = pytest.importorskip("redis.exceptions")
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff

    from fastapi_cachex.backends import AsyncRedisCacheBackend

    backend = AsyncRedisCacheBackend(
        host=REDIS_HOST, port=UNCONNECTED_PORT, retry=Retry(NoBackoff(), 0)
    )
    try:
        with pytest.raises(BackendUnavailableError) as excinfo:
            await backend.set("k", ENTRY)
    finally:
        await backend.aclose()
    _assert_unavailable(excinfo.value, redis_exceptions.ConnectionError)


async def test_memcached_refused_connection_then_server_out_of_rotation() -> None:
    pytest.importorskip("pymemcache")
    from pymemcache.exceptions import MemcacheError

    backend = MemcachedBackend(servers=[f"127.0.0.1:{_closed_port()}"])
    try:
        with pytest.raises(BackendUnavailableError) as refused:
            await backend.get("k")
        # The failed server is now out of rotation for a second.
        with pytest.raises(BackendUnavailableError) as down:
            await backend.set("k", ENTRY)
    finally:
        await backend.aclose()
    _assert_unavailable(refused.value, ConnectionRefusedError)
    assert isinstance(refused.value, OSError)
    assert refused.value.errno == refused.value.__cause__.errno  # type: ignore[union-attr]
    _assert_unavailable(down.value, MemcacheError)
    assert "All servers seem to be down" in str(down.value)


async def test_cache_manager_lets_it_through() -> None:
    """The case #260 is about: one except clause, whatever the backend."""
    pytest.importorskip("pymemcache")
    backend = MemcachedBackend(servers=[f"127.0.0.1:{_closed_port()}"])
    try:
        with pytest.raises(BackendUnavailableError):
            await CacheManager(backend).get("report")
    finally:
        await backend.aclose()


async def test_other_errors_pass_through_unchanged() -> None:
    error = ValueError("bad input")

    @translate_unavailable(lambda e: isinstance(e, OSError))
    async def fails() -> None:
        raise error

    with pytest.raises(ValueError, match="bad input") as excinfo:
        await fails()
    assert excinfo.value is error


async def test_a_nested_call_is_translated_once() -> None:
    translate = translate_unavailable(lambda e: isinstance(e, OSError))

    @translate
    async def inner() -> None:
        raise ConnectionResetError(104, "reset")

    @translate
    async def outer() -> None:
        await inner()

    with pytest.raises(BackendUnavailableError) as excinfo:
        await outer()
    assert type(excinfo.value.__cause__) is ConnectionResetError


def test_the_subclass_is_built_once_per_type() -> None:
    first = as_unavailable(TimeoutError("slow"))
    second = as_unavailable(TimeoutError("slower"))
    assert type(first) is type(second)
    assert type(first) is not type(as_unavailable(ConnectionResetError("reset")))
    assert str(second) == "slower"


def test_it_survives_pickling() -> None:
    """The per-type classes are built at runtime; pickling rebuilds them."""
    error = as_unavailable(ConnectionResetError(104, "reset"))
    restored = pickle.loads(pickle.dumps(error))  # noqa: S301 - our own bytes
    assert type(restored) is type(error)
    assert isinstance(restored, ConnectionResetError)
    assert restored.errno == 104
    assert str(restored) == str(error)
