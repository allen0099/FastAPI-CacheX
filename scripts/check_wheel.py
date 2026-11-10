"""Check a built wheel before it is published (#241).

Every test runs from the source tree, so a packaging mistake (a missing
``py.typed`` or licence, a subpackage left out, the tests shipped by accident)
shows up only for someone who installs the wheel. ``release.yml`` and the
**Lint** workflow run this on the wheel ``uv build`` produced::

    uv run --isolated --no-project --with dist/<wheel> \
        python scripts/check_wheel.py dist/<wheel>

Run in an environment where the wheel is the only source of the package, it
also imports ``fastapi_cachex`` and checks that the import came from the
installed wheel and reports the wheel's version. It has to be run as a script
by path, as above: that puts ``scripts/`` on ``sys.path``, not the checkout,
which ``python -m`` or ``python -c`` from the repository root would.

The environment's dependencies are resolved fresh, not from ``uv.lock``: it is
what someone installing the release gets, so a new FastAPI or pydantic release
that breaks the import fails here.
"""

import argparse
import importlib
import sys
import zipfile
from pathlib import Path

PACKAGE = "fastapi_cachex"

# Files a wheel without them would still import, so nothing else notices.
# Renaming one of these modules means updating this list in the same change.
REQUIRED = (
    f"{PACKAGE}/__init__.py",
    f"{PACKAGE}/py.typed",
    f"{PACKAGE}/backends/__init__.py",
    f"{PACKAGE}/backends/memory.py",
    f"{PACKAGE}/backends/redis.py",
    f"{PACKAGE}/backends/memcached.py",
)

# Top-level names that belong to the repository, not to the package.
FORBIDDEN_TOP_LEVEL = frozenset({"tests", "examples", "scripts", "docs", "i18n"})


def wheel_version(names: list[str]) -> str | None:
    """The version in the wheel's ``<name>-<version>.dist-info`` directory."""
    for name in names:
        top = name.split("/", 1)[0]
        if top.endswith(".dist-info"):
            return top.removesuffix(".dist-info").split("-", 1)[1]
    return None


def check_contents(wheel: Path) -> list[str]:
    """What is wrong with the files in ``wheel``; empty when nothing is."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = next(
            (
                archive.read(name).decode("utf-8")
                for name in names
                if name.endswith(".dist-info/METADATA")
            ),
            "",
        )

    problems = [f"missing {name}" for name in REQUIRED if name not in names]
    if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
        problems.append("missing the licence in dist-info/licenses/")
    problems.extend(
        f"missing {field} in METADATA"
        for field in ("License-Expression:", "Requires-Python:")
        if not any(line.startswith(field) for line in metadata.splitlines())
    )
    shipped = {name.split("/", 1)[0] for name in names}
    problems.extend(
        f"ships {name}/, which is not part of the package"
        for name in sorted(shipped & FORBIDDEN_TOP_LEVEL)
    )
    return problems


def check_import(wheel: Path) -> list[str]:
    """What is wrong with importing the installed package; empty when nothing is."""
    with zipfile.ZipFile(wheel) as archive:
        expected = wheel_version(archive.namelist())
    module = importlib.import_module(PACKAGE)
    problems = []
    origin = Path(module.__file__ or "").resolve()
    if "site-packages" not in origin.parts:
        problems.append(
            f"imported {origin}, not an installed copy: run this in an "
            "environment that has only the wheel"
        )
    if module.__version__ != expected:
        problems.append(
            f"{PACKAGE}.__version__ is {module.__version__!r}, "
            f"the wheel is {expected!r}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Check the wheel named on the command line; 0 when it is fine."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path)
    parser.add_argument(
        "--no-import",
        action="store_true",
        help="check the files only, without importing the installed package",
    )
    args = parser.parse_args(argv)

    problems = check_contents(args.wheel)
    if not args.no_import:
        problems += check_import(args.wheel)
    for problem in problems:
        sys.stderr.write(f"{args.wheel.name}: {problem}\n")
    if not problems:
        sys.stdout.write(f"{args.wheel.name}: ok\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
