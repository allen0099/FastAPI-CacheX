"""The common misuses raise errors that name the fix (#581)."""

from typing import Any

import pytest
from fastapi import Request

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheManager
from fastapi_cachex import cache
from fastapi_cachex import cached
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
    with pytest.raises(RequestNotFoundError, match=r"outside a FastAPI route.*@cached"):
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
