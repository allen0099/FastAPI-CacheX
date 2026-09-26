"""Tests for the package-level exceptions."""

import importlib

import pytest

from fastapi_cachex.exceptions import CacheXError


def test_cache_error_access_warns_and_still_works() -> None:
    """Test accessing the deprecated CacheError warns (#163)."""
    exceptions = importlib.import_module("fastapi_cachex.exceptions")

    with pytest.warns(DeprecationWarning, match="CacheError is deprecated") as record:
        cache_error = exceptions.CacheError

    assert record[0].filename == __file__
    assert cache_error.__name__ == "CacheError"
    assert issubclass(cache_error, CacheXError)
    with pytest.raises(CacheXError):
        raise cache_error


def test_cache_error_from_import_warns() -> None:
    """Test ``from fastapi_cachex.exceptions import CacheError`` warns (#163)."""
    with pytest.warns(DeprecationWarning, match="removed in version 0.4.0"):
        from fastapi_cachex.exceptions import CacheError  # noqa: F401


def test_unknown_attribute_still_raises_attribute_error() -> None:
    """Test the module ``__getattr__`` only handles CacheError."""
    exceptions = importlib.import_module("fastapi_cachex.exceptions")

    with pytest.raises(AttributeError, match="has no attribute 'Missing'"):
        _ = exceptions.Missing
