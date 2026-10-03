"""``fastapi_cachex.cache`` after it was split into smaller modules (#422).

The module keeps every name it defined before, and every part of ``@cache``
still logs under the documented ``fastapi_cachex.cache`` logger.
"""

import importlib
import logging

import pytest

# `fastapi_cachex.cache` is shadowed by the `cache` decorator on the package.
cache_module = importlib.import_module("fastapi_cachex.cache")

EXPORTED = [
    "AsyncResponseCallable",
    "CacheControl",
    "HandlerCallable",
    "build_cache_key",
    "cache",
    "default_key_builder",
    "get_response",
    "invalidate",
    "logger",
]


@pytest.mark.parametrize("name", EXPORTED)
def test_the_module_still_exports(name: str) -> None:
    assert hasattr(cache_module, name)
    assert name in cache_module.__all__


def test_the_package_reexports_the_same_objects() -> None:
    package = importlib.import_module("fastapi_cachex")

    for name in ("build_cache_key", "default_key_builder", "invalidate"):
        assert getattr(package, name) is getattr(cache_module, name)
    assert package.cache is cache_module.cache


@pytest.mark.parametrize(
    "module",
    ["fastapi_cachex.cache", "fastapi_cachex._key_builders"],
)
def test_every_part_logs_under_the_documented_logger(module: str) -> None:
    logger = importlib.import_module(module).logger

    assert logger is logging.getLogger("fastapi_cachex.cache")
