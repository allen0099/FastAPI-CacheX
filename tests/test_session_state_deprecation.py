"""Session and OAuth state are deprecated in 0.4.0 (#420).

Each package warns once per process, on first import, so these tests run the
import in a fresh interpreter.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import fastapi_cachex


def _run(script: str, tmp_path: Path) -> str:
    """Run ``script`` as a file in a new interpreter; return its stderr."""
    path = tmp_path / "app.py"
    path.write_text(script)
    result = subprocess.run(  # noqa: S603 - fixed interpreter, script written by the test
        [sys.executable, "-W", "always::FutureWarning", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stderr


def test_plain_import_does_not_warn(tmp_path: Path) -> None:
    stderr = _run(
        "import fastapi_cachex\n"
        "from fastapi_cachex import cache, CacheManager, CacheLock\n",
        tmp_path,
    )
    assert "FutureWarning" not in stderr


@pytest.mark.parametrize(
    ("script", "package"),
    [
        ("from fastapi_cachex.session import SessionConfig\n", "session"),
        ("from fastapi_cachex.session.config import SessionConfig\n", "session"),
        ("from fastapi_cachex import SessionConfig\n", "session"),
        ("import fastapi_cachex\nfastapi_cachex.get_session\n", "session"),
        ("from fastapi_cachex.state import StateManager\n", "state"),
        ("from fastapi_cachex import StateManager\n", "state"),
    ],
)
def test_import_warns_at_the_importing_line(
    script: str, package: str, tmp_path: Path
) -> None:
    stderr = _run(script, tmp_path)
    line = script.count("\n")
    assert (
        f"app.py:{line}: FutureWarning: fastapi_cachex.{package} is deprecated "
        "and will be removed in fastapi-cachex 0.5.0"
    ) in stderr
    assert "MIGRATING_0_4/#session-state-deprecated" in stderr


def test_state_does_not_import_session(tmp_path: Path) -> None:
    stderr = _run("from fastapi_cachex.state import StateManager\n", tmp_path)
    assert "fastapi_cachex.session is deprecated" not in stderr


@pytest.mark.parametrize("name", sorted(fastapi_cachex._DEPRECATED_NAMES))
def test_deprecated_name_resolves_but_is_not_exported(name: str) -> None:
    module = __import__(fastapi_cachex._DEPRECATED_NAMES[name], fromlist=[name])
    assert getattr(fastapi_cachex, name) is getattr(module, name)
    assert name not in fastapi_cachex.__all__


def test_unknown_name_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        fastapi_cachex.nope  # noqa: B018
