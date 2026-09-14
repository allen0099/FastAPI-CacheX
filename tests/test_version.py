"""Tests for the package's `__version__` attribute."""

from importlib.metadata import version

import fastapi_cachex


def test_version_is_exported() -> None:
    """`__version__` is a non-empty string and part of the public API."""
    assert isinstance(fastapi_cachex.__version__, str)
    assert fastapi_cachex.__version__
    assert "__version__" in fastapi_cachex.__all__


def test_version_matches_installed_distribution() -> None:
    """The attribute reports the installed distribution, not a hard-coded copy.

    Reading it from metadata is the point: a literal in `__init__.py` would
    drift from `pyproject.toml` the first time a release bumps only one of them.
    """
    assert fastapi_cachex.__version__ == version("fastapi-cachex")
