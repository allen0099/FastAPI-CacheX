"""Promote the `## [Unreleased]` changelog section into a released version.

`release.yml` calls this so that one manual dispatch does the whole cut: the
heading is renamed, a fresh empty `## [Unreleased]` is opened above it, the
compare links at the bottom are rewritten, and the promoted section is written
out to be used verbatim as the GitHub release body.

Two rules drive the implementation:

* The previous version is **read from the existing headings**, never derived
  from the new one. 0.3.3 was never released, so `[0.3.4]` has to compare
  against `v0.3.2`; arithmetic on the version number would silently produce a
  compare link against a tag that does not exist.
* An empty `## [Unreleased]` is an error, not an empty release note. The
  changelog is maintained by hand, so "nothing was written down" and "nothing
  changed" look identical from here, and only the former is likely.

Run it directly to see what a release would produce::

    uv run python scripts/changelog_release.py --version 0.3.5 --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

UNRELEASED = "Unreleased"

_HEADING = re.compile(
    r"^## \[(?P<version>[^\]]+)\](?: - (?P<date>\S+))?[ \t]*$",
    re.MULTILINE,
)
_LINK = re.compile(r"^\[(?P<label>[^\]]+)\]: (?P<url>\S+)[ \t]*$", re.MULTILINE)
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class ChangelogError(RuntimeError):
    """The changelog is not in a state that can be released."""


def _find_unreleased(text: str) -> tuple[re.Match[str], list[re.Match[str]]]:
    """Return the `Unreleased` heading and every released heading below it."""
    headings = list(_HEADING.finditer(text))
    if not headings:
        msg = "no `## [...]` headings found; is this a Keep a Changelog file?"
        raise ChangelogError(msg)
    if headings[0]["version"] != UNRELEASED:
        msg = (
            f"the first `## [...]` heading is `{headings[0]['version']}`, "
            f"expected `{UNRELEASED}`"
        )
        raise ChangelogError(msg)
    return headings[0], headings[1:]


def _section_body(text: str, heading: re.Match[str], next_start: int) -> str:
    return text[heading.end() : next_start]


def _links(text: str) -> dict[str, re.Match[str]]:
    return {match["label"]: match for match in _LINK.finditer(text)}


def _base_url(unreleased_link: re.Match[str]) -> str:
    url = unreleased_link["url"]
    base, separator, _ = url.partition("/compare/")
    if not separator:
        msg = f"the [{UNRELEASED}] link is not a compare link: {url}"
        raise ChangelogError(msg)
    return base


def promote(text: str, version: str, date: str) -> tuple[str, str]:
    """Cut `version` out of the `Unreleased` section.

    Args:
        text: The full contents of `CHANGELOG.md`.
        version: The version being released, without a leading `v`.
        date: The release date, `YYYY-MM-DD`.

    Returns:
        The rewritten changelog, and the promoted section to use as the
        release body.

    Raises:
        ChangelogError: The version is malformed or already released, the
            `Unreleased` section is empty or missing, or the link definitions
            at the bottom are not in the expected shape.
    """
    if not _VERSION.match(version):
        msg = f"`{version}` is not a MAJOR.MINOR.PATCH version"
        raise ChangelogError(msg)

    unreleased, released = _find_unreleased(text)
    if any(heading["version"] == version for heading in released):
        msg = f"`## [{version}]` is already in the changelog"
        raise ChangelogError(msg)

    links = _links(text)
    if UNRELEASED not in links:
        msg = f"no `[{UNRELEASED}]:` link definition at the bottom of the file"
        raise ChangelogError(msg)
    base = _base_url(links[UNRELEASED])

    body_end = released[0].start() if released else links[UNRELEASED].start()
    body = _section_body(text, unreleased, body_end)
    if not body.strip():
        msg = (
            f"`## [{UNRELEASED}]` is empty. Write down what changed before "
            f"releasing {version}; see docs/DEVELOPMENT.md#releasing."
        )
        raise ChangelogError(msg)

    # The previous version comes from the headings, not from arithmetic on
    # `version`: skipped numbers are real (0.3.3 was never released).
    previous = released[0]["version"] if released else None
    if previous is None:
        new_link = f"[{version}]: {base}/releases/tag/v{version}"
    else:
        new_link = f"[{version}]: {base}/compare/v{previous}...v{version}"

    rewritten = (
        text[: unreleased.start()]
        + f"## [{UNRELEASED}]\n\n## [{version}] - {date}"
        + text[unreleased.end() : links[UNRELEASED].start()]
        + f"[{UNRELEASED}]: {base}/compare/v{version}...HEAD\n"
        + new_link
        + text[links[UNRELEASED].end() :]
    )
    return rewritten, body.strip() + "\n"


def _today() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="version being released")
    parser.add_argument("--date", default=_today(), help="release date (YYYY-MM-DD)")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="path to the changelog",
    )
    parser.add_argument(
        "--release-notes",
        type=Path,
        help="write the promoted section here, for use as the release body",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the release body instead of writing anything",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    args = _parse_args(argv)
    try:
        rewritten, body = promote(
            args.changelog.read_text(encoding="utf-8"),
            args.version,
            args.date,
        )
    except (ChangelogError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)  # noqa: T201
        return 1

    if args.dry_run:
        print(body, end="")  # noqa: T201
        return 0

    args.changelog.write_text(rewritten, encoding="utf-8")
    if args.release_notes is not None:
        args.release_notes.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
