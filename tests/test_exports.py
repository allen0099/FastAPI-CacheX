"""Exceptions raised by the core are importable from the package (#160)."""

import pytest

import fastapi_cachex
from fastapi_cachex import exceptions


@pytest.mark.parametrize(
    "name",
    ["BackendNotFoundError", "CacheXError", "ProxyNotSetError", "RequestNotFoundError"],
)
def test_core_exception_is_exported(name: str) -> None:
    assert name in fastapi_cachex.__all__
    assert getattr(fastapi_cachex, name) is getattr(exceptions, name)
