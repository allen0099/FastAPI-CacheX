"""Tests for the release-time changelog promotion script."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.changelog_release import ChangelogError
from scripts.changelog_release import assemble
from scripts.changelog_release import main
from scripts.changelog_release import promote
from scripts.changelog_release import read_fragments
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

    # The pending fragments go in first, exactly as the release merges them,
    # so a malformed fragment fails the pull request that adds it.
    fragments = read_fragments(changelog.parent / "changelog.d")
    text = assemble(text, fragments)

    # Between releases `## [Unreleased]` is usually empty and every entry
    # waits in a fragment. With neither, promote() rightly refuses; give it an
    # entry so the rest of the file is still checked.
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
    notes = release_notes(body, "0.9.9", "2026-09-14")
    assert notes.startswith("### ")
    # Every pending fragment reaches the release page with its issue link.
    for fragment in fragments:
        assert f"[#{fragment.issue}]({BASE}/issues/{fragment.issue})" in notes
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


# Changelog fragments


def _fragments(tmp_path: Path, files: dict[str, str]) -> Path:
    directory = tmp_path / "changelog.d"
    directory.mkdir(exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def _link(issue: int) -> str:
    return f"([#{issue}]({BASE}/issues/{issue}))"


def test_fragments_are_ordered_by_section_issue_and_number(tmp_path: Path):
    directory = _fragments(
        tmp_path,
        {
            "9.security.md": "**Nine.**",
            "65.added.2.md": "**Sixty-five, second.**",
            "65.added.md": "**Sixty-five.**",
            "7.added.md": "**Seven.**",
            "8.fixed.md": "**Eight.**",
            "65.added.10.md": "**Sixty-five, tenth.**",
        },
    )

    fragments = read_fragments(directory)

    assert [fragment.path.name for fragment in fragments] == [
        "7.added.md",
        "65.added.md",
        "65.added.2.md",
        "65.added.10.md",
        "8.fixed.md",
        "9.security.md",
    ]


def test_a_missing_fragment_directory_holds_no_fragments(tmp_path: Path):
    assert read_fragments(tmp_path / "changelog.d") == []


def test_the_readme_and_dotfiles_are_not_fragments(tmp_path: Path):
    directory = _fragments(
        tmp_path,
        {"README.md": "# Not an entry\n", ".gitkeep": "", "1.added.md": "**A.**"},
    )

    assert [fragment.path.name for fragment in read_fragments(directory)] == [
        "1.added.md"
    ]


def test_assemble_puts_fragments_under_their_headings_in_keep_a_changelog_order(
    tmp_path: Path,
):
    empty = CHANGELOG.replace(f"### Added\n\n{ENTRY}\n", "")
    directory = _fragments(
        tmp_path,
        {
            "5.security.md": "**Five.** Details.",
            "3.fixed.md": "**Three.** Details.",
            "4.removed.md": "**Four.**",
            "2.changed.md": "**Two.**",
            "6.deprecated.md": "**Six.**",
            "1.added.md": "**One.**",
        },
    )

    assembled = assemble(empty, read_fragments(directory))

    assert assembled.split("## [0.3.4]")[0] == (
        "# Changelog\n\n## [Unreleased]\n\n"
        f"### Added\n\n- **One.** {_link(1)}\n\n"
        f"### Changed\n\n- **Two.** {_link(2)}\n\n"
        f"### Deprecated\n\n- **Six.** {_link(6)}\n\n"
        f"### Removed\n\n- **Four.** {_link(4)}\n\n"
        f"### Fixed\n\n- **Three.** Details. {_link(3)}\n\n"
        f"### Security\n\n- **Five.** Details. {_link(5)}\n\n"
    )
    # Nothing below the Unreleased section is touched.
    assert assembled.split("## [0.3.4]")[1] == empty.split("## [0.3.4]")[1]


def test_assemble_indents_the_rest_of_a_multi_line_fragment(tmp_path: Path):
    directory = _fragments(
        tmp_path,
        {
            "12.fixed.md": (
                "**Stop `z()` from losing\nentries.** It dropped them.\n\n"
                "  - nested\n    - deeper\n\nMore.\n"
            ),
        },
    )

    assembled = assemble(CHANGELOG, read_fragments(directory))

    assert (
        "### Fixed\n\n- **Stop `z()` from losing\n  entries.** It dropped them.\n\n"
        f"    - nested\n      - deeper\n\n  More. {_link(12)}\n\n## [0.3.4]"
    ) in assembled
    _, body = promote(assembled, "0.3.5", "2026-09-14")
    assert f"- Stop `z()` from losing entries. {_link(12)}\n" in release_notes(
        body, "0.3.5", "2026-09-14"
    )


def test_a_fragment_ending_in_a_nested_list_gets_its_link_on_its_own(
    tmp_path: Path,
):
    directory = _fragments(
        tmp_path, {"12.fixed.md": "**X.** Either:\n\n  - a\n  - b\n"}
    )

    assembled = assemble(CHANGELOG, read_fragments(directory))

    assert (
        f"- **X.** Either:\n\n    - a\n    - b\n\n  {_link(12)}\n\n## [0.3.4]"
    ) in assembled
    _, body = promote(assembled, "0.3.5", "2026-09-14")
    assert f"- X. {_link(12)}\n" in release_notes(body, "0.3.5", "2026-09-14")


def test_assemble_merges_with_entries_written_by_hand(tmp_path: Path):
    handwritten = CHANGELOG.replace(
        f"### Added\n\n{ENTRY}\n",
        "### Documentation\n\n- **Docs.**\n\n"
        f"### Added\n\n{ENTRY}\n\n"
        "### Fixed\n\n- **By hand.**\n  Wrapped. ([#3](x))\n",
    )
    directory = _fragments(
        tmp_path,
        {
            "2.added.md": "**From a fragment.**",
            "4.fixed.md": "**Fixed by a fragment.**",
            "5.changed.md": "**Changed by a fragment.**",
        },
    )

    assembled = assemble(handwritten, read_fragments(directory))

    assert assembled.split("## [0.3.4]")[0] == (
        "# Changelog\n\n## [Unreleased]\n\n"
        f"### Added\n\n{ENTRY}\n- **From a fragment.** {_link(2)}\n\n"
        f"### Changed\n\n- **Changed by a fragment.** {_link(5)}\n\n"
        "### Fixed\n\n- **By hand.**\n  Wrapped. ([#3](x))\n"
        f"- **Fixed by a fragment.** {_link(4)}\n\n"
        # A heading Keep a Changelog does not define stays, after its sections.
        "### Documentation\n\n- **Docs.**\n\n"
    )


def test_assemble_keeps_loose_entries_above_the_first_heading(tmp_path: Path):
    loose = CHANGELOG.replace(
        f"### Added\n\n{ENTRY}\n", "- **Loose.**\n\n### Added\n\n- **Kept.**\n"
    )
    directory = _fragments(tmp_path, {"1.added.md": "**New.**"})

    assembled = assemble(loose, read_fragments(directory))

    assert (
        "## [Unreleased]\n\n- **Loose.**\n\n"
        f"### Added\n\n- **Kept.**\n- **New.** {_link(1)}\n\n## [0.3.4]"
    ) in assembled


def test_assemble_without_fragments_changes_nothing():
    assert assemble(CHANGELOG, []) == CHANGELOG


def test_assemble_takes_the_issue_link_base_from_the_changelog(tmp_path: Path):
    fork = CHANGELOG.replace(BASE, "https://github.com/someone/fork")
    directory = _fragments(tmp_path, {"7.added.md": "**Seven.**"})

    assembled = assemble(fork, read_fragments(directory))

    assert "- **Seven.** ([#7](https://github.com/someone/fork/issues/7))" in assembled


def test_fragments_flow_into_the_release_notes(tmp_path: Path):
    directory = _fragments(
        tmp_path,
        {
            "20.added.md": "**Twenty.**\nWith details that the notes drop.",
            "21.fixed.md": f"**Twenty-one.** See also [#22]({BASE}/pull/22).",
        },
    )
    assembled = assemble(CHANGELOG, read_fragments(directory))

    _, body = promote(assembled, "0.3.5", "2026-09-14")

    assert release_notes(body, "0.3.5", "2026-09-14") == (
        f"### Added\n\n- A thing. ([#1]({BASE}/issues/1))\n"
        f"- Twenty. {_link(20)}\n\n"
        f"### Fixed\n\n- Twenty-one. {_link(21)}\n\n"
        "**Full changelog**: "
        "https://fastapi-cachex.readthedocs.io/en/stable/changelog/#035-2026-09-14\n"
    )


def test_an_empty_unreleased_section_with_fragments_can_be_released(tmp_path: Path):
    empty = CHANGELOG.replace(f"### Added\n\n{ENTRY}\n", "")
    directory = _fragments(tmp_path, {"1.fixed.md": "**One.**"})

    rewritten, body = promote(
        assemble(empty, read_fragments(directory)), "0.3.5", "2026-09-14"
    )

    assert body == f"### Fixed\n\n- **One.** {_link(1)}\n"
    assert "## [Unreleased]\n\n## [0.3.5] - 2026-09-14\n\n### Fixed\n" in rewritten


def test_fragments_need_the_unreleased_link_for_their_issue_links(tmp_path: Path):
    without_link = CHANGELOG.replace(
        f"[Unreleased]: {BASE}/compare/v0.3.4...HEAD\n", ""
    )
    directory = _fragments(tmp_path, {"1.fixed.md": "**One.**"})

    with pytest.raises(ChangelogError, match="link definition"):
        assemble(without_link, read_fragments(directory))


@pytest.mark.parametrize(
    ("name", "text", "error"),
    [
        ("12.md", "**X.**", "not a fragment name"),
        ("12.added.txt", "**X.**", "not a fragment name"),
        ("issue-12.added.md", "**X.**", "not a fragment name"),
        ("012.added.md", "**X.**", "not a fragment name"),
        ("12.added.1.md", "**X.**", "not a fragment name"),
        ("12.added.two.md", "**X.**", "not a fragment name"),
        ("12.feature.md", "**X.**", "unknown section `feature`"),
        ("12.Added.md", "**X.**", "unknown section `Added`"),
        ("12.added.md", " \n\n", "the file is empty"),
        ("12.added.md", "- **X.** Details.", "drop the leading `- `"),
        ("12.added.md", "X. Details.", "bold one-line summary"),
        ("12.added.md", "Details. **X.**", "bold one-line summary"),
        ("12.added.md", "**X.**\n- **Y.**", "would start a new entry"),
        ("12.added.md", "**X.**\n### Fixed", "would start a new entry or heading"),
        ("12.added.md", f"**X.** ([#12]({BASE}/issues/12))", "remove the link to #12"),
        ("12.added.md", b"**\xff**", "not UTF-8"),
    ],
)
def test_a_malformed_fragment_fails_the_release_before_anything_changes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    text: str | bytes,
    error: str,
):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    directory = _fragments(tmp_path, {"1.added.md": "**Fine.**"})
    bad = directory / name
    if isinstance(text, bytes):
        bad.write_bytes(text)
    else:
        bad.write_text(text, encoding="utf-8")
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
    stderr = capsys.readouterr().err
    assert "malformed changelog fragments" in stderr
    assert name in stderr
    assert error in stderr
    assert changelog.read_text(encoding="utf-8") == CHANGELOG
    assert sorted(path.name for path in directory.iterdir()) == sorted(
        ["1.added.md", name]
    )
    assert not notes.exists()


def test_a_directory_in_the_fragment_directory_is_an_error(tmp_path: Path):
    directory = _fragments(tmp_path, {})
    (directory / "12.added.md").mkdir()

    with pytest.raises(ChangelogError, match="not a fragment name"):
        read_fragments(directory)


def test_every_malformed_fragment_is_listed_at_once(tmp_path: Path):
    directory = _fragments(
        tmp_path,
        {"1.feature.md": "**A.**", "2.added.md": "B.", "3.added.md": "**C.**"},
    )

    with pytest.raises(ChangelogError) as error:
        read_fragments(directory)

    assert "1.feature.md" in str(error.value)
    assert "2.added.md" in str(error.value)
    assert "3.added.md" not in str(error.value)


def test_main_merges_the_fragments_and_then_deletes_them(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    directory = _fragments(
        tmp_path,
        {
            "README.md": "# Fragments\n",
            ".gitkeep": "",
            "2.added.md": "**Two.**",
            "3.fixed.md": "**Three.**",
        },
    )
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
    text = changelog.read_text(encoding="utf-8")
    assert (
        f"## [0.3.5] - 2026-09-14\n\n### Added\n\n{ENTRY}\n- **Two.** {_link(2)}\n\n"
        f"### Fixed\n\n- **Three.** {_link(3)}\n\n## [0.3.4]"
    ) in text
    assert f"- Two. {_link(2)}" in notes.read_text(encoding="utf-8")
    assert f"- Three. {_link(3)}" in notes.read_text(encoding="utf-8")
    assert sorted(path.name for path in directory.iterdir()) == [
        ".gitkeep",
        "README.md",
    ]


def test_main_reads_fragments_from_the_given_directory(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "2.added.md").write_text("**Two.**", encoding="utf-8")

    exit_code = main(
        [
            "--version",
            "0.3.5",
            "--changelog",
            str(changelog),
            "--fragments",
            str(elsewhere),
        ]
    )

    assert exit_code == 0
    assert f"- **Two.** {_link(2)}" in changelog.read_text(encoding="utf-8")
    assert list(elsewhere.iterdir()) == []


def test_main_dry_run_keeps_the_fragments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    directory = _fragments(tmp_path, {"2.added.md": "**Two.**"})

    exit_code = main(["--version", "0.3.5", "--changelog", str(changelog), "--dry-run"])

    assert exit_code == 0
    assert f"- Two. {_link(2)}" in capsys.readouterr().out
    assert changelog.read_text(encoding="utf-8") == CHANGELOG
    assert (directory / "2.added.md").exists()
