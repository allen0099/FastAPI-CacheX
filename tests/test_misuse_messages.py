"""The common misuses raise errors that name the fix (#581)."""

import asyncio
import functools
import logging
import sys
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import Request

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheLock
from fastapi_cachex import CacheManager
from fastapi_cachex import cache
from fastapi_cachex import cached
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.exceptions import RequestNotFoundError
from fastapi_cachex.exceptions import TTLTypeError


async def _handler() -> int:
    return 1


@pytest.mark.parametrize("decorator", [cache, cached])
def test_a_decorator_without_parentheses_says_so(decorator: Any) -> None:
    with pytest.raises(TTLTypeError, match=r"with parentheses"):
        decorator(_handler)


def test_a_non_callable_ttl_gets_no_hint() -> None:
    with pytest.raises(TTLTypeError) as info:
        cache("60")  # type: ignore[arg-type]
    assert "parentheses" not in str(info.value)


async def test_cache_outside_a_route_names_cached() -> None:
    with pytest.raises(
        RequestNotFoundError, match=r"outside a FastAPI HTTP route.*@cached"
    ):
        await cache(ttl=60)(_handler)()


def test_a_missing_backend_says_how_to_set_one() -> None:
    BackendProxy.set(None)
    with pytest.raises(BackendNotFoundError, match=r"BackendProxy\.set\("):
        CacheManager()


def test_a_fastapi_cache2_key_builder_is_refused_when_applied() -> None:
    def key_builder(
        func: Any,
        namespace: str,
        request: Request,
        response: Any,
        args: Any,
        kwargs: Any,
    ) -> str:
        return "k"

    with pytest.raises(CacheXError, match=r"key_builder\(request\).*fastapi-cache2"):
        cache(ttl=60, key_builder=key_builder)(_handler)  # type: ignore[arg-type]


def test_a_key_builder_with_optional_extras_is_accepted() -> None:
    def key_builder(request: Request, suffix: str = "") -> str:
        return "k" + suffix

    cache(ttl=60, key_builder=key_builder)(_handler)


def test_a_wraps_adapter_around_a_fastapi_cache2_builder_is_accepted() -> None:
    """The adapter's own signature counts, not the one it wraps."""

    def fc2_builder(func: Any, namespace: str, request: Request) -> str:
        return request.url.path

    @functools.wraps(fc2_builder)
    def adapter(request: Request) -> str:
        return fc2_builder(None, "", request)

    cache(ttl=60, key_builder=adapter)(_handler)


@pytest.mark.parametrize("target", [APIRouter, FastAPI], ids=["router", "app"])
def test_cache_on_an_app_or_router_is_refused(target: type) -> None:
    """Both are callable, so the decorator took them and cached nothing."""
    with pytest.raises(CacheXError, match=r"route function, not .*@router\.get"):
        cache(ttl=60)(target())


async def test_an_undecodable_value_warns_once_per_get_or_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The re-read under the lock logged the same failure a second time."""
    manager = CacheManager(backend=MemoryBackend())
    await manager.set("k", "text")

    async def factory() -> int:
        return 5

    with caplog.at_level(logging.DEBUG, logger="fastapi_cachex.manager"):
        assert await manager.get_or_set("k", factory, type_=int) == 5
    failures = [
        r for r in caplog.records if "does not match type_=int" in r.getMessage()
    ]
    assert [r.levelno for r in failures if r.levelno >= logging.WARNING] == [
        logging.WARNING
    ]
    # Every read is still logged at DEBUG, with the key.
    assert len([r for r in failures if r.levelno == logging.DEBUG]) == 2


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("huge", id="int-over-the-digit-limit"),
        pytest.param("cycle", id="cycle"),
    ],
)
async def test_a_value_json_refuses_with_valueerror_raises_typeerror(
    value: str,
) -> None:
    """APP_CACHE.md says a value that cannot be stored raises TypeError."""
    stored: Any
    if value == "huge":
        stored = 10**5000
    else:
        stored = []
        stored.append(stored)
    manager = CacheManager(backend=MemoryBackend())
    limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)  # the default, whatever the runner set
    try:
        with pytest.raises(TypeError, match="cannot be stored as JSON") as info:
            await manager.set("k", stored)
    finally:
        sys.set_int_max_str_digits(limit)
    # What it raised before, so code catching that keeps working.
    assert isinstance(info.value, ValueError)
    assert await manager.get("k") is None


async def test_a_value_that_appears_while_polling_still_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silencing every later read hid a bad value written meanwhile."""
    backend = MemoryBackend()
    manager = CacheManager(backend=backend)
    # Another caller holds the key's lock, so this call polls.
    holder = CacheLock(name=manager._cache_key("k"), ttl=5, backend=backend)
    assert await holder.acquire(blocking=False)

    async def factory() -> int:
        return 5

    async def meanwhile() -> None:
        await asyncio.sleep(0.1)
        await manager.set("k", "text")  # a value the call cannot decode
        await asyncio.sleep(0.3)
        await holder.release()

    with caplog.at_level(logging.DEBUG, logger="fastapi_cachex.manager"):
        result, _ = await asyncio.gather(
            manager.get_or_set("k", factory, type_=int, lock=True, wait_timeout=3),
            meanwhile(),
        )
    assert result == 5
    failures = [
        r for r in caplog.records if "does not match type_=int" in r.getMessage()
    ]
    assert [r.levelno for r in failures].count(logging.WARNING) == 1
    assert len(failures) > 2  # polled more than once


def test_a_key_builder_without_arguments_names_its_signature() -> None:
    """The message says what is wrong, not inspect's "too many" (#625)."""
    with pytest.raises(CacheXError) as raised:
        cache(ttl=60, key_builder=lambda: "k")(_handler)  # type: ignore[arg-type, misc]

    message = str(raised.value)
    assert "signature () cannot be called with one argument" in message
    assert "too many" not in message
