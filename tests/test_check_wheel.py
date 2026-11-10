"""``scripts/check_wheel.py`` catches a wheel that was packaged wrongly (#241)."""

import importlib
import types
import zipfile
from pathlib import Path

import pytest

from scripts.check_wheel import REQUIRED
from scripts.check_wheel import check_contents
from scripts.check_wheel import check_import
from scripts.check_wheel import main

DIST_INFO = "fastapi_cachex-1.2.3.dist-info"
METADATA = "Version: 1.2.3\nLicense-Expression: Apache-2.0\nRequires-Python: >=3.10\n"
GOOD = {
    **dict.fromkeys(REQUIRED, ""),
    f"{DIST_INFO}/METADATA": METADATA,
    f"{DIST_INFO}/licenses/LICENSE": "Apache",
}


def build(tmp_path: Path, files: dict[str, str]) -> Path:
    wheel = tmp_path / "fastapi_cachex-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel


def test_a_complete_wheel_has_no_problems(tmp_path: Path) -> None:
    assert check_contents(build(tmp_path, GOOD)) == []


@pytest.mark.parametrize(
    ("remove", "problem"),
    [
        ("fastapi_cachex/py.typed", "missing fastapi_cachex/py.typed"),
        (
            "fastapi_cachex/backends/redis.py",
            "missing fastapi_cachex/backends/redis.py",
        ),
        (f"{DIST_INFO}/licenses/LICENSE", "missing the licence"),
    ],
)
def test_a_missing_file_is_reported(tmp_path: Path, remove: str, problem: str) -> None:
    files = {name: content for name, content in GOOD.items() if name != remove}

    assert any(problem in found for found in check_contents(build(tmp_path, files)))


@pytest.mark.parametrize("field", ["License-Expression", "Requires-Python"])
def test_missing_metadata_is_reported(tmp_path: Path, field: str) -> None:
    metadata = "\n".join(
        line for line in METADATA.splitlines() if not line.startswith(field)
    )
    files = {**GOOD, f"{DIST_INFO}/METADATA": metadata}

    assert check_contents(build(tmp_path, files)) == [f"missing {field}: in METADATA"]


def test_a_wheel_without_metadata_is_reported(tmp_path: Path) -> None:
    files = {k: v for k, v in GOOD.items() if not k.endswith("METADATA")}

    assert len(check_contents(build(tmp_path, files))) == 2


def test_shipping_the_tests_is_reported(tmp_path: Path) -> None:
    files = {**GOOD, "tests/test_x.py": "", "examples/app.py": ""}

    assert check_contents(build(tmp_path, files)) == [
        "ships examples/, which is not part of the package",
        "ships tests/, which is not part of the package",
    ]


def installed(monkeypatch: pytest.MonkeyPatch, path: str, version: str) -> None:
    module = types.SimpleNamespace(__file__=path, __version__=version)
    monkeypatch.setattr(importlib, "import_module", lambda _: module)


def test_an_import_from_the_installed_wheel_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(
        monkeypatch, "/venv/lib/site-packages/fastapi_cachex/__init__.py", "1.2.3"
    )

    assert check_import(build(tmp_path, GOOD)) == []


def test_an_import_from_the_source_tree_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Then the wheel was not what got tested."""
    installed(monkeypatch, "/repo/fastapi_cachex/__init__.py", "1.2.3")

    assert "not an installed copy" in check_import(build(tmp_path, GOOD))[0]


def test_a_version_that_differs_from_the_wheel_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(
        monkeypatch, "/venv/lib/site-packages/fastapi_cachex/__init__.py", "0.0.0"
    )

    assert "the wheel is '1.2.3'" in check_import(build(tmp_path, GOOD))[0]


def test_main_reports_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = {k: v for k, v in GOOD.items() if k != "fastapi_cachex/py.typed"}
    wheel = build(tmp_path, files)

    assert main([str(wheel), "--no-import"]) == 1
    assert "missing fastapi_cachex/py.typed" in capsys.readouterr().err


def test_main_passes_a_good_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    installed(
        monkeypatch, "/venv/lib/site-packages/fastapi_cachex/__init__.py", "1.2.3"
    )

    assert main([str(build(tmp_path, GOOD))]) == 0
    assert capsys.readouterr().out.endswith(": ok\n")


def test_main_fails_a_wheel_that_was_not_what_got_imported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The import check is on unless ``--no-import`` is passed."""
    installed(monkeypatch, "/repo/fastapi_cachex/__init__.py", "1.2.3")
    wheel = build(tmp_path, GOOD)

    assert main([str(wheel)]) == 1
    assert "not an installed copy" in capsys.readouterr().err
    assert main([str(wheel), "--no-import"]) == 0


def test_a_licence_outside_dist_info_does_not_count(tmp_path: Path) -> None:
    files = {k: v for k, v in GOOD.items() if not k.endswith("licenses/LICENSE")}
    files["fastapi_cachex/LICENSE"] = "Apache"

    assert check_contents(build(tmp_path, files)) == [
        "missing the licence in dist-info/licenses/"
    ]
