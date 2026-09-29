"""Promote the `## [Unreleased]` changelog section into a released version.

`release.yml` calls this so that one manual dispatch does the whole cut: the
heading is renamed, a fresh empty `## [Unreleased]` is opened above it, the
compare links at the bottom are rewritten, and a short version of the promoted
section is written out as the GitHub release body.

The changelog keeps the full story of each change; the release page lists one
line per change. Every entry therefore opens with a bold one-line summary::

    - **Add `CacheManager.add()` for store-if-absent writes.** It uses the
      same key prefix ... ([#65](https://github.com/.../issues/65))

and the release body is those summaries with their issue links, grouped under
the same `###` headings, followed by a link to the full entries on the
documentation site.

Three rules drive the implementation:

* The previous version is **read from the existing headings**, never derived
  from the new one. 0.3.3 was never released, so `[0.3.4]` has to compare
  against `v0.3.2`; arithmetic on the version number would silently produce a
  compare link against a tag that does not exist.
* An empty `## [Unreleased]` is an error, not an empty release note. The
  entries are written by hand, so "nothing was written down" and "nothing
  changed" look identical from here, and only the former is likely.
* An entry without a bold summary is an error too, for the same reason: the
  release would otherwise publish a line nobody chose.

Entries normally arrive as fragments rather than as edits to `CHANGELOG.md`,
so that parallel pull requests do not conflict on one section. A fragment is
`changelog.d/<issue>.<section>.md` (or `<issue>.<section>.<n>.md`, n >= 2, for
a further entry about the same issue and section) holding one entry without
its leading `- ` and without its issue link::

    **Add `CacheManager.add()` for store-if-absent writes.** It uses the
    same key prefix ...

Before promoting, the fragments are merged into `## [Unreleased]` under their
`###` headings, in Keep a Changelog order and after any entry written there by
hand; each gets its `- ` and its issue link from the file name. They are
deleted once the new changelog is written. Every fragment is checked before
anything is written, so a malformed one fails the release with nothing
changed. `changelog.d/README.md` and dotfiles are not fragments.

Run it directly to see what a release would produce::

    uv run python scripts/changelog_release.py --version 0.3.5 --dry-run
"""

from __future__ import annotations

import argparse
import dataclasses
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
_SUMMARY = re.compile(r"^\*\*(?P<summary>.+?)\*\*", re.DOTALL)
_ISSUE_LINK = re.compile(r"\(\[#\d+\]\([^)\s]+\)(?:, \[#\d+\]\([^)\s]+\))*\)")

# Keep a Changelog's sections, in its order; fragment file names use these.
SECTIONS = ("added", "changed", "deprecated", "removed", "fixed", "security")
FRAGMENTS_DIR = "changelog.d"
FRAGMENTS_README = "README.md"
_FRAGMENT_NAME = re.compile(
    r"^(?P<issue>[1-9]\d*)\.(?P<section>[^.]+)(?:\.(?P<n>[2-9]|[1-9]\d+))?\.md$",
)

# Where the full entries are read. `stable` is rebuilt from the tag a release
# pushes, and the anchor is the one the site generates for `## [X.Y.Z] - DATE`.
DOCS_CHANGELOG = "https://fastapi-cachex.readthedocs.io/en/stable/changelog/"


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


def _unreleased_body_end(
    released: list[re.Match[str]], links: dict[str, re.Match[str]]
) -> int:
    """Return where the `Unreleased` section's body ends."""
    if UNRELEASED not in links:
        msg = f"no `[{UNRELEASED}]:` link definition at the bottom of the file"
        raise ChangelogError(msg)
    return released[0].start() if released else links[UNRELEASED].start()


@dataclasses.dataclass(frozen=True)
class Fragment:
    """One changelog entry waiting in `changelog.d/`."""

    path: Path
    issue: int
    section: str
    number: int
    text: str

    def entry(self, base: str) -> list[str]:
        """Render the fragment as a `- ` list entry with its issue link.

        Args:
            base: The repository URL, `https://github.com/OWNER/REPO`.

        Returns:
            The entry's lines, continuation lines indented under the bullet.
        """
        lines = [line.rstrip() for line in self.text.strip().splitlines()]
        link = f"([#{self.issue}]({base}/issues/{self.issue}))"
        if lines[-1][0].isspace():
            # Ending in a nested list: a paragraph of its own, not the last item.
            lines += ["", link]
        else:
            lines[-1] += f" {link}"
        rest = [f"  {line}" if line else "" for line in lines[1:]]
        return [f"- {lines[0]}", *rest]


def _check_fragment(path: Path) -> Fragment | str:
    """Parse one fragment, or return why it cannot be released."""
    name = _FRAGMENT_NAME.match(path.name)
    if not path.is_file() or name is None:
        return (
            "not a fragment name; expected `<issue>.<section>.md` or "
            "`<issue>.<section>.<n>.md` (n >= 2)"
        )
    section = name["section"]
    if section not in SECTIONS:
        return f"unknown section `{section}`; use one of {', '.join(SECTIONS)}"
    issue = int(name["issue"])
    try:
        text = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        return "not UTF-8 text"
    problem = _fragment_text_problem(text, issue, section)
    if problem is not None:
        return problem
    return Fragment(path, issue, section, int(name["n"] or 1), text)


def _fragment_text_problem(text: str, issue: int, section: str) -> str | None:
    """Return why a fragment's text cannot be released, if it cannot."""
    if not text:
        return "the file is empty"
    if text.startswith(("- ", "* ")):
        return "drop the leading `- `; the release adds it"
    if _SUMMARY.match(text) is None:
        return (
            "must open with a bold one-line summary, `**What changed.** The details...`"
        )
    for line in text.splitlines()[1:]:
        if line.startswith(("- ", "* ")) or re.match(r"#{1,6} ", line):
            return (
                f"`{line}` would start a new entry or heading; write one entry "
                f"per file (another goes in `{issue}.{section}.2.md`), and "
                "indent a nested list by two spaces"
            )
    if re.search(rf"\[#{issue}\]", text):
        return f"remove the link to #{issue}; the release appends it from the file name"
    return None


def read_fragments(directory: Path) -> list[Fragment]:
    """Read and check every fragment in `directory`.

    Args:
        directory: The fragment directory, normally `changelog.d`. A missing
            directory holds no fragments.

    Returns:
        The fragments, ordered by section, issue and number.

    Raises:
        ChangelogError: A file in the directory, other than its README and
            dotfiles, is not a well-formed fragment. Every problem is listed.
    """
    if not directory.is_dir():
        return []
    fragments: list[Fragment] = []
    problems: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.name == FRAGMENTS_README or path.name.startswith("."):
            continue
        checked = _check_fragment(path)
        if isinstance(checked, str):
            problems.append(f"  - {path}: {checked}")
        else:
            fragments.append(checked)
    if problems:
        msg = (
            f"malformed changelog fragments (see {directory}/{FRAGMENTS_README}):\n"
            + "\n".join(problems)
        )
        raise ChangelogError(msg)
    return sorted(
        fragments,
        key=lambda fragment: (
            SECTIONS.index(fragment.section),
            fragment.issue,
            fragment.number,
        ),
    )


def _trim(lines: list[str]) -> list[str]:
    """Drop blank lines at both ends."""
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def assemble(text: str, fragments: list[Fragment]) -> str:
    """Merge fragments into the `Unreleased` section.

    Sections come out in Keep a Changelog order, followed by any other
    hand-written `###` section in its original order. Within a section, the
    hand-written entries come first and are kept verbatim, then the fragments
    by issue number.

    Args:
        text: The full contents of `CHANGELOG.md`.
        fragments: The fragments, as returned by `read_fragments`.

    Returns:
        The changelog with the fragments in it; unchanged if there are none.

    Raises:
        ChangelogError: The `Unreleased` section or its link definition is
            missing or malformed.
    """
    if not fragments:
        return text
    unreleased, released = _find_unreleased(text)
    links = _links(text)
    body_end = _unreleased_body_end(released, links)
    base = _base_url(links[UNRELEASED])

    preamble: list[str] = []
    titles: dict[str, str] = {}
    blocks: dict[str, list[str]] = {}
    current = preamble
    for line in _section_body(text, unreleased, body_end).splitlines():
        if line.startswith("### "):
            title = line.removeprefix("### ").strip()
            titles.setdefault(title.lower(), title)
            current = blocks.setdefault(title.lower(), [])
        else:
            current.append(line)
    blocks = {key: _trim(block) for key, block in blocks.items()}
    for fragment in fragments:
        titles.setdefault(fragment.section, fragment.section.capitalize())
        blocks.setdefault(fragment.section, []).extend(fragment.entry(base))

    order = [key for key in SECTIONS if key in blocks]
    order += [key for key in blocks if key not in SECTIONS]
    parts = ["\n".join(_trim(preamble))] if _trim(preamble) else []
    parts += [f"### {titles[key]}\n\n" + "\n".join(blocks[key]) for key in order]
    return (
        text[: unreleased.end()]
        + "\n\n"
        + "\n\n".join(parts)
        + "\n\n"
        + text[body_end:]
    )


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
    body_end = _unreleased_body_end(released, links)
    base = _base_url(links[UNRELEASED])

    body = _section_body(text, unreleased, body_end)
    if not body.strip():
        msg = (
            f"`## [{UNRELEASED}]` is empty and changelog.d/ has no fragments. "
            f"Write down what changed before releasing {version}; see "
            "docs/DEVELOPMENT.md#releasing."
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


def _split_notice(body: str) -> tuple[str, str]:
    """Split the notice off the top of a changelog section.

    The notice is the text above the first `###` heading and the first `- `
    entry, such as "This is the last 0.3.x release". Returns
    `(notice, rest)`; the notice is `""` when the section has none.
    """
    lines = body.splitlines()
    end = next(
        (index for index, line in enumerate(lines) if line.startswith(("### ", "- "))),
        len(lines),
    )
    return "\n".join(_trim(lines[:end])), "\n".join(lines[end:])


def _entries(body: str) -> list[tuple[str, str]]:
    """Split a changelog section into `(### heading, entry text)` pairs.

    An entry is a top-level `- ` bullet with everything indented under it,
    nested lists included. Entries above the first heading get `""`.
    """
    entries: list[tuple[str, list[str]]] = []
    heading = ""
    for line in body.splitlines():
        if line.startswith("### "):
            heading = line
        elif line.startswith("- "):
            entries.append((heading, [line[2:]]))
        elif line.strip() and not line[0].isspace():
            msg = f"`{line}` is neither a `###` heading nor a `- ` list entry"
            raise ChangelogError(msg)
        elif entries and entries[-1][0] == heading:
            entries[-1][1].append(line.strip())
    return [(section, " ".join(filter(None, lines))) for section, lines in entries]


def release_notes(body: str, version: str, date: str) -> str:
    """Shorten a promoted section to one line per entry, for the release page.

    Args:
        body: The promoted section, as returned by `promote`.
        version: The version being released.
        date: The release date, `YYYY-MM-DD`.

    Returns:
        The section's notice, if any, verbatim; each entry's bold summary and
        issue links, under the section's `###` headings; then a link to the
        full entries on the documentation site.

    Raises:
        ChangelogError: The section has no entries, a line below the notice
            is neither a heading nor an entry, or an entry does not open with
            a bold summary.
    """
    notice, rest = _split_notice(body)
    entries = _entries(rest)
    if not entries:
        msg = (
            "the release has no changelog entries; a notice alone is nothing to release"
        )
        raise ChangelogError(msg)

    lines: list[str] = [notice, ""] if notice else []
    missing: list[str] = []
    heading = ""
    for section, text in entries:
        summary = _SUMMARY.match(text)
        if summary is None:
            missing.append(text[:70])
            continue
        if section != heading:
            heading = section
            lines += ["", heading, ""] if lines and lines[-1] else [heading, ""]
        links = " ".join(_ISSUE_LINK.findall(text))
        line = " ".join(summary["summary"].split())
        lines.append(f"- {line} {links}".rstrip())
    if missing:
        listed = "\n".join(f"  - {text}..." for text in missing)
        msg = (
            "every changelog entry must open with a bold one-line summary, "
            "`- **What changed.** Details...`; these do not:\n" + listed
        )
        raise ChangelogError(msg)

    anchor = f"{version.replace('.', '')}-{date}"
    lines += ["", f"**Full changelog**: {DOCS_CHANGELOG}#{anchor}"]
    return "\n".join(lines) + "\n"


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
        "--fragments",
        type=Path,
        help=(
            "directory of changelog fragments to merge in, then delete "
            "(default: changelog.d next to the changelog)"
        ),
    )
    parser.add_argument(
        "--release-notes",
        type=Path,
        help="write the release body (one line per entry) here",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the release body instead of writing or deleting anything",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    args = _parse_args(argv)
    # Everything that can fail runs before anything is written or deleted.
    try:
        fragments = read_fragments(
            args.fragments or args.changelog.parent / FRAGMENTS_DIR
        )
        rewritten, body = promote(
            assemble(args.changelog.read_text(encoding="utf-8"), fragments),
            args.version,
            args.date,
        )
        notes = release_notes(body, args.version, args.date)
    except (ChangelogError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)  # noqa: T201
        return 1

    if args.dry_run:
        print(notes, end="")  # noqa: T201
        return 0

    args.changelog.write_text(rewritten, encoding="utf-8")
    if args.release_notes is not None:
        args.release_notes.write_text(notes, encoding="utf-8")
    for fragment in fragments:
        fragment.path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
