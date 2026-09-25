"""Tests for the release-time changelog promotion script."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.changelog_release import ChangelogError
from scripts.changelog_release import main
from scripts.changelog_release import promote
from scripts.changelog_release import release_notes

BASE = "https://github.com/allen0099/FastAPI-CacheX"
ENTRY = f"- **A thing.** With the details. ([#1]({BASE}/issues/1))"
NOTES = (
    f"### Added\n\n- A thing. ([#1]({BASE}/issues/1))\n\n"
    "**Full changelog**: "
    "https://fastapi-cachex.readthedocs.io/en/stable/changelog/#035-2026-09-14\n"
)

CHANGELOG = f"""# Changelog

## [Unreleased]

### Added

{ENTRY}

## [0.3.4] - 2026-09-05

### Fixed

- An older thing.

[Unreleased]: {BASE}/compare/v0.3.4...HEAD
[0.3.4]: {BASE}/compare/v0.3.2...v0.3.4
"""


def _links(text: str) -> list[str]:
    """The link definitions at the bottom, not inline `[text](url)` links."""
    return [line for line in text.splitlines() if re.match(r"^\[[^\]]+\]: ", line)]


def test_promote_renames_the_heading_and_opens_a_fresh_unreleased():
    rewritten, _ = promote(CHANGELOG, "0.3.5", "2026-09-14")

    headings = [line for line in rewritten.splitlines() if line.startswith("## ")]
    assert headings == [
        "## [Unreleased]",
        "## [0.3.5] - 2026-09-14",
        "## [0.3.4] - 2026-09-05",
    ]
    # The fresh section is empty: the released content moved down with its heading.
    assert "## [Unreleased]\n\n## [0.3.5] - 2026-09-14\n\n### Added\n" in rewritten


def test_promote_rewrites_the_compare_links():
    rewritten, _ = promote(CHANGELOG, "0.3.5", "2026-09-14")

    assert _links(rewritten) == [
        f"[Unreleased]: {BASE}/compare/v0.3.5...HEAD",
        f"[0.3.5]: {BASE}/compare/v0.3.4...v0.3.5",
        f"[0.3.4]: {BASE}/compare/v0.3.2...v0.3.4",
    ]


def test_promote_returns_only_the_unreleased_section_as_the_release_body():
    _, body = promote(CHANGELOG, "0.3.5", "2026-09-14")

    assert body == f"### Added\n\n{ENTRY}\n"
    assert "older thing" not in body


def test_the_previous_version_comes_from_the_headings_not_from_arithmetic():
    """0.3.3 was never released, so 0.3.4 must compare against v0.3.2."""
    skipped = f"""# Changelog

## [Unreleased]

- Something.

## [0.3.2] - 2026-07-29

- Older.

[Unreleased]: {BASE}/compare/v0.3.2...HEAD
[0.3.2]: {BASE}/compare/v0.3.1...v0.3.2
"""

    rewritten, _ = promote(skipped, "0.3.4", "2026-09-05")

    assert f"[0.3.4]: {BASE}/compare/v0.3.2...v0.3.4" in rewritten
    assert "v0.3.3" not in rewritten


def test_the_first_release_links_to_the_tag_instead_of_a_comparison():
    first = f"""# Changelog

## [Unreleased]

- Everything.

[Unreleased]: {BASE}/compare/v0.1.0...HEAD
"""

    rewritten, _ = promote(first, "0.1.0", "2026-01-01")

    assert f"[0.1.0]: {BASE}/releases/tag/v0.1.0" in rewritten


def test_version_numbers_in_prose_are_left_alone():
    """Only headings and link definitions are rewritten."""
    prose = CHANGELOG.replace(ENTRY, "- `X` is deprecated and goes away in 0.3.5.")

    rewritten, body = promote(prose, "0.3.5", "2026-09-14")

    assert "- `X` is deprecated and goes away in 0.3.5." in rewritten
    assert "- `X` is deprecated and goes away in 0.3.5." in body


def test_an_empty_unreleased_section_is_an_error():
    empty = CHANGELOG.replace(f"### Added\n\n{ENTRY}\n", "")

    with pytest.raises(ChangelogError, match="is empty"):
        promote(empty, "0.3.5", "2026-09-14")


def test_an_already_released_version_is_an_error():
    with pytest.raises(ChangelogError, match="already in the changelog"):
        promote(CHANGELOG, "0.3.4", "2026-09-14")


def test_a_malformed_version_is_an_error():
    with pytest.raises(ChangelogError, match=r"MAJOR\.MINOR\.PATCH"):
        promote(CHANGELOG, "v0.3.5", "2026-09-14")


def test_a_missing_unreleased_heading_is_an_error():
    released_only = CHANGELOG.replace("## [Unreleased]", "## [0.3.9] - 2026-09-14", 1)

    with pytest.raises(ChangelogError, match="expected `Unreleased`"):
        promote(released_only, "0.4.0", "2026-09-14")


def test_a_file_without_headings_is_an_error():
    with pytest.raises(ChangelogError, match="Keep a Changelog"):
        promote("# Changelog\n\nnothing here\n", "0.3.5", "2026-09-14")


def test_a_missing_unreleased_link_is_an_error():
    without_link = CHANGELOG.replace(
        f"[Unreleased]: {BASE}/compare/v0.3.4...HEAD\n", ""
    )

    with pytest.raises(ChangelogError, match="link definition"):
        promote(without_link, "0.3.5", "2026-09-14")


def test_an_unreleased_link_that_is_not_a_comparison_is_an_error():
    wrong_link = CHANGELOG.replace(
        f"[Unreleased]: {BASE}/compare/v0.3.4...HEAD",
        f"[Unreleased]: {BASE}/tree/master",
    )

    with pytest.raises(ChangelogError, match="not a compare link"):
        promote(wrong_link, "0.3.5", "2026-09-14")


def test_the_repository_changelog_can_be_released():
    """The real file, so the script cannot drift away from what it operates on."""
    changelog = Path(__file__).parent.parent / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")

    # Read the latest release off the file instead of naming it, so the test
    # keeps passing after every release rather than breaking on the next one.
    latest = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)
    assert latest is not None

    # Right after a release `## [Unreleased]` is empty, which promote()
    # rightly refuses. Give it an entry so the rest of the file is still checked.
    text = re.sub(
        r"^## \[Unreleased\]\n\s*(?=^## \[)",
        "## [Unreleased]\n\n### Fixed\n\n- **Placeholder entry.**\n\n",
        text,
        count=1,
        flags=re.MULTILINE,
    )

    rewritten, body = promote(text, "0.9.9", "2026-09-14")

    assert rewritten.startswith("# Changelog\n")
    assert text.count("removed in 0.3.5") == rewritten.count("removed in 0.3.5")
    assert f"[0.9.9]: {BASE}/compare/v{latest[1]}...v0.9.9" in rewritten
    assert body.startswith("### ")
    # Every entry waiting in `Unreleased` has the summary the release page needs.
    assert release_notes(body, "0.9.9", "2026-09-14").startswith("### ")
    # Every released heading still has a link definition, and vice versa.
    headings = {
        line.removeprefix("## [").split("]")[0]
        for line in rewritten.splitlines()
        if line.startswith("## [")
    }
    labels = {line.removeprefix("[").split("]")[0] for line in _links(rewritten)}
    assert headings == labels


def test_main_writes_the_changelog_and_the_release_notes(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    notes = tmp_path / "release-notes.md"

    exit_code = main(
        [
            "--version",
            "0.3.5",
            "--date",
            "2026-09-14",
            "--changelog",
            str(changelog),
            "--release-notes",
            str(notes),
        ]
    )

    assert exit_code == 0
    assert "## [0.3.5] - 2026-09-14" in changelog.read_text(encoding="utf-8")
    assert notes.read_text(encoding="utf-8") == NOTES


def test_main_dry_run_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    exit_code = main(
        [
            "--version",
            "0.3.5",
            "--date",
            "2026-09-14",
            "--changelog",
            str(changelog),
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == NOTES
    assert changelog.read_text(encoding="utf-8") == CHANGELOG


def test_main_defaults_the_date_to_today(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")

    assert main(["--version", "0.3.5", "--changelog", str(changelog)]) == 0

    import datetime

    today = datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat()
    assert f"## [0.3.5] - {today}" in changelog.read_text(encoding="utf-8")


def test_main_reports_the_reason_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        CHANGELOG.replace(f"### Added\n\n{ENTRY}\n", ""), encoding="utf-8"
    )

    exit_code = main(["--version", "0.3.5", "--changelog", str(changelog)])

    assert exit_code == 1
    assert "is empty" in capsys.readouterr().err
    assert changelog.read_text(encoding="utf-8").count("## [Unreleased]") == 1


def test_main_reports_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    exit_code = main(["--version", "0.3.5", "--changelog", str(tmp_path / "nope.md")])

    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_a_second_release_cannot_open_a_second_unreleased():
    """Running the promotion twice is refused, not quietly duplicated."""
    rewritten, _ = promote(CHANGELOG, "0.3.5", "2026-09-14")

    assert rewritten.count("## [Unreleased]") == 1
    assert rewritten.count("[Unreleased]: ") == 1

    with pytest.raises(ChangelogError, match="is empty"):
        promote(rewritten, "0.3.6", "2026-09-15")


def test_release_notes_keep_one_line_per_entry_under_its_heading():
    body = f"""### Added

- **Add `x()` for doing X.** It does X by way of Y, which takes a long
  explanation over several lines. ([#10]({BASE}/issues/10))
- **Add `y()`.** No issue for this one.

### Fixed

- **Stop `z()` from
  losing entries.** The summary above wraps; the details carry a list:
  - a first detail with a [link](https://example.com);
  - a second detail.

  ([#11]({BASE}/issues/11), [#12]({BASE}/pull/12))
"""
    notes = release_notes(body, "1.2.3", "2026-10-01")

    assert notes == (
        "### Added\n\n"
        f"- Add `x()` for doing X. ([#10]({BASE}/issues/10))\n"
        "- Add `y()`.\n\n"
        "### Fixed\n\n"
        f"- Stop `z()` from losing entries. ([#11]({BASE}/issues/11), [#12]({BASE}/pull/12))\n\n"
        "**Full changelog**: "
        "https://fastapi-cachex.readthedocs.io/en/stable/changelog/#123-2026-10-01\n"
    )


def test_release_notes_list_every_entry_without_a_summary():
    body = """### Added

- **Has one.** Details.
- Has none, so the release would publish this whole sentence.

### Fixed

- `thing()` works now.
"""
    with pytest.raises(ChangelogError, match="bold one-line summary") as error:
        release_notes(body, "1.2.3", "2026-10-01")

    assert "Has none, so the release" in str(error.value)
    assert "`thing()` works now." in str(error.value)
    assert "Has one." not in str(error.value)


def test_release_notes_reject_text_that_is_not_an_entry():
    body = "### Added\n\nA paragraph instead of a list.\n"

    with pytest.raises(ChangelogError, match="neither a `###` heading"):
        release_notes(body, "1.2.3", "2026-10-01")


def test_release_notes_accept_entries_before_any_heading():
    notes = release_notes("- **Loose entry.** Details.\n", "1.2.3", "2026-10-01")

    assert notes.startswith("- Loose entry.\n\n**Full changelog**: ")


def test_main_refuses_to_release_an_entry_without_a_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG.replace(ENTRY, "- A thing."), encoding="utf-8")
    notes = tmp_path / "release-notes.md"

    exit_code = main(
        [
            "--version",
            "0.3.5",
            "--changelog",
            str(changelog),
            "--release-notes",
            str(notes),
        ]
    )

    assert exit_code == 1
    assert "bold one-line summary" in capsys.readouterr().err
    assert "## [0.3.5]" not in changelog.read_text(encoding="utf-8")
    assert not notes.exists()
