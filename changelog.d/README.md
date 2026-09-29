# Changelog fragments

Pull requests do not edit `CHANGELOG.md`. Each one that changes behaviour, adds
public API, or fixes something a user could have hit adds a fragment here
instead, and the release merges the fragments into `CHANGELOG.md` and deletes
them. Two open pull requests therefore never conflict over the changelog.

## File name

`<issue>.<section>.md`, for example `65.added.md`, where `<section>` is one of
`added`, `changed`, `deprecated`, `removed`, `fixed`, `security`.

A second entry for the same issue and section goes in `<issue>.<section>.2.md`,
a third in `<issue>.<section>.3.md`, and so on. One file holds one entry.

## Content

One changelog entry, opening with a bold one-line summary. Leave out the
leading `- ` and the issue link: the release adds both, the link taken from the
file name. Wrap lines however you like; indent a nested list by two spaces.

```markdown
**`CacheManager.add()` stores a value only if the key is absent.** It uses the
same key prefix, JSON encoding and `default_ttl` as `set()`.
```

becomes, under `### Added`:

```markdown
- **`CacheManager.add()` stores a value only if the key is absent.** It uses the
  same key prefix, JSON encoding and `default_ttl` as `set()`. ([#65](https://github.com/allen0099/FastAPI-CacheX/issues/65))
```

The release notes list only the bold summary, so write it for someone deciding
whether the release matters to them.

A notice for the top of the release notes, such as "This is the last 0.3.x
release", is not a fragment: write it into `CHANGELOG.md` directly under
`## [Unreleased]`, above the first heading or entry.

`tests/test_changelog_release.py` checks every fragment here, so a malformed one
(a bad name, an unknown section, no bold summary, a leading `- `, its own issue
link) fails CI in the pull request that adds it. This README and dotfiles are
not fragments. See
[Releasing](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#releasing).
