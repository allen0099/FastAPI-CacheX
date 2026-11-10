"""Python 3.10 support ends in 0.6.0; 0.5.x warns on import (#491)."""

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from fastapi_cachex import _warn_python_310


def test_python_310_warns() -> None:
    with pytest.warns(FutureWarning, match=r"0\.6\.0 will require Python 3\.11"):
        _warn_python_310((3, 10))


def test_python_311_does_not_warn() -> None:
    # pyproject.toml ignores this warning, so record it instead of relying on
    # filterwarnings = ["error"].
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _warn_python_310((3, 11))
    assert caught == []


def test_import_warns_at_the_importing_line_only_on_python_310(
    tmp_path: Path,
) -> None:
    """A fresh interpreter: the warning names the application's import line."""
    app = tmp_path / "app.py"
    app.write_text("import fastapi_cachex\n")
    result = subprocess.run(  # noqa: S603 - fixed interpreter, script written by the test
        [sys.executable, "-W", "always::FutureWarning", str(app)],
        capture_output=True,
        text=True,
        check=True,
    )
    if sys.version_info >= (3, 11):
        assert "FutureWarning" not in result.stderr
    else:
        assert f"{app}:1: FutureWarning: fastapi-cachex 0.6.0" in result.stderr
