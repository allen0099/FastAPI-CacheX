"""An invalid ``ttl`` raises the same exceptions wherever it is passed (#337).

``TTLTypeError`` and ``TTLValueError`` are ``InvalidTTLError``, a
``CacheXError``, and also the ``TypeError`` / ``ValueError`` the backends,
``CacheManager`` and ``CacheLock`` raised before, so every existing ``except``
clause keeps catching them.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest

from fastapi_cachex import CacheLock
from fastapi_cachex import CacheManager
from fastapi_cachex import CacheXError
from fastapi_cachex import InvalidTTLError
from fastapi_cachex import TTLTypeError
from fastapi_cachex import TTLValueError
from fastapi_cachex import cache
from fastapi_cachex import cached
from fastapi_cachex.backends.base import MAX_TTL
from fastapi_cachex.backends.memcached import _expiry
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.types import CacheEntry

ENTRY = CacheEntry(fingerprint="e", content=b"v")

BAD_TTLS = [
    pytest.param(1.5, TTLTypeError, TypeError, id="float"),
    pytest.param(True, TTLTypeError, TypeError, id="bool"),
    pytest.param("60", TTLTypeError, TypeError, id="str"),
    pytest.param(-1, TTLValueError, ValueError, id="negative"),
    pytest.param(MAX_TTL + 1, TTLValueError, ValueError, id="too-large"),
    pytest.param(timedelta(seconds=1.5), TTLValueError, ValueError, id="fraction"),
]


def _assert_ttl_error(
    excinfo: pytest.ExceptionInfo[BaseException],
    error: type[InvalidTTLError],
    builtin: type[Exception],
) -> None:
    assert type(excinfo.value) is error
    assert isinstance(excinfo.value, InvalidTTLError)
    assert isinstance(excinfo.value, CacheXError)
    assert isinstance(excinfo.value, builtin)


def test_the_classes_keep_the_built_in_bases() -> None:
    assert issubclass(InvalidTTLError, CacheXError)
    assert issubclass(TTLTypeError, InvalidTTLError)
    assert issubclass(TTLTypeError, TypeError)
    assert issubclass(TTLValueError, InvalidTTLError)
    assert issubclass(TTLValueError, ValueError)


@pytest.mark.parametrize(("ttl", "error", "builtin"), BAD_TTLS)
async def test_backend_set(
    ttl: Any, error: type[InvalidTTLError], builtin: type[Exception]
) -> None:
    backend = MemoryBackend()
    try:
        with pytest.raises(builtin) as excinfo:
            await backend.set("k", ENTRY, ttl=ttl)
    finally:
        await backend.aclose()
    _assert_ttl_error(excinfo, error, builtin)


@pytest.mark.parametrize(("ttl", "error", "builtin"), BAD_TTLS)
async def test_cache_manager(
    ttl: Any, error: type[InvalidTTLError], builtin: type[Exception]
) -> None:
    backend = MemoryBackend()
    try:
        with pytest.raises(builtin) as built:
            CacheManager(backend, default_ttl=ttl)
        _assert_ttl_error(built, error, builtin)
        with pytest.raises(builtin) as stored:
            await CacheManager(backend).set("k", 1, ttl=ttl)
        _assert_ttl_error(stored, error, builtin)
    finally:
        await backend.aclose()


@pytest.mark.parametrize(("ttl", "error", "builtin"), BAD_TTLS)
async def test_cache_lock(
    ttl: Any, error: type[InvalidTTLError], builtin: type[Exception]
) -> None:
    backend = MemoryBackend()
    try:
        with pytest.raises(builtin) as excinfo:
            CacheLock("job", ttl=ttl, backend=backend)
    finally:
        await backend.aclose()
    _assert_ttl_error(excinfo, error, builtin)


@pytest.mark.parametrize(("ttl", "error", "builtin"), BAD_TTLS)
def test_cached_decorator(
    ttl: Any, error: type[InvalidTTLError], builtin: type[Exception]
) -> None:
    with pytest.raises(builtin) as excinfo:
        cached(ttl=ttl)
    _assert_ttl_error(excinfo, error, builtin)


# @cache allows 0 (max-age=0), so a zero ttl is not among its bad values.
@pytest.mark.parametrize(("ttl", "error", "builtin"), BAD_TTLS)
@pytest.mark.parametrize(
    "apply",
    [
        pytest.param(lambda ttl: cache(ttl=ttl), id="ttl"),
        pytest.param(
            lambda ttl: cache(ttl=60, stale="revalidate", stale_ttl=ttl),
            id="stale_ttl",
        ),
    ],
)
def test_cache_decorator(
    apply: Callable[[Any], object],
    ttl: Any,
    error: type[InvalidTTLError],
    builtin: type[Exception],
) -> None:
    with pytest.raises(CacheXError) as excinfo:
        apply(ttl)
    _assert_ttl_error(excinfo, error, builtin)


def test_memcached_expiry_past_2038() -> None:
    with pytest.raises(ValueError, match="2038") as excinfo:
        _expiry(MAX_TTL)
    _assert_ttl_error(excinfo, TTLValueError, ValueError)
