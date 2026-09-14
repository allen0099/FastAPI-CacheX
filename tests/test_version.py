"""Tests for the package's `__version__` attribute."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

import pytest

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


def test_version_falls_back_when_the_distribution_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing from an uninstalled source tree reports a dev version.

    There is no metadata to read in that case, and raising would make a plain
    `import fastapi_cachex` fail; the fallback is what keeps it importable.
    """

    def _raise(_name: str) -> str:
        raise PackageNotFoundError(_name)

    monkeypatch.setattr("fastapi_cachex.version", _raise)
    assert fastapi_cachex._read_version() == "0.0.0.dev0"
