"""Exceptions raised by the core are importable from the package (#160)."""

import importlib

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


@pytest.mark.parametrize(
    "name", ["SessionManager", "FastAPICacheXSessionMiddleware", "StateManager"]
)
def test_removed_session_and_state_names_are_gone(name: str) -> None:
    """0.5.0 removed sessions and OAuth state (#421): no lazy deprecated names."""
    assert name not in fastapi_cachex.__all__
    with pytest.raises(AttributeError, match=name):
        getattr(fastapi_cachex, name)


@pytest.mark.parametrize("module", ["fastapi_cachex.session", "fastapi_cachex.state"])
def test_removed_session_and_state_packages_are_gone(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)
