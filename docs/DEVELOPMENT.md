# Development Guide

This document contains information for developers who want to contribute to FastAPI-CacheX.

## Environment & Package Management

This project uses UV for package and environment management. Always run commands through UV.

1. Sync development dependencies:

```bash
uv sync --group dev
```

2. Run commands via UV (examples below).

## Running Tests

1. Run unit tests:

```bash
uv run pytest
```

2. Run tests with coverage report:

```bash
uv run pytest --cov=fastapi_cachex --cov-report=term-missing
```

### Redis and Memcached tests are opt-in

`uv run pytest` skips every test that needs a real Redis or Memcached. That is
deliberate, and the reason matters: those suites are **destructive**. Their
fixtures call `backend.clear()` around each test, which deletes every
`fastapi_cachex:`-prefixed key on the Redis they connect to, and — since the
Memcached protocol has no key enumeration — issues `flush_all` on the Memcached
they connect to, wiping the entire server.

So there is no default port. If the ports defaulted to 6379 and 11211, running
the suite on a machine that happens to have either service up would silently
destroy the developer's own data, and a developer who runs Redis locally *and*
has this library checked out is exactly the person whose `fastapi_cachex:` keys
are their own application's cache.

Start throwaway servers and name their ports to opt in:

```bash
./scripts/start-redis-server.sh        # 6380
./scripts/start-memcache-server.sh     # 11212

CACHEX_TEST_REDIS_PORT=6380 CACHEX_TEST_MEMCACHED_PORT=11212 uv run pytest
```

`CACHEX_TEST_REDIS_HOST` and `CACHEX_TEST_MEMCACHED_HOST` default to
`127.0.0.1`. Setting a port is you stating that the server on it is disposable —
so do not point these at anything you care about. When a port is set but nothing
is listening, the suites skip and say so.

A run with nothing opted in still clears the coverage gate (`fail_under = 90`)
at about 94.5%, but only because the rest of the suite carries it — `redis.py`
alone drops to roughly 42%. The margin is thin, so the first place an untested
line shows up as a failure is a local opted-out run, not CI, which sets both
variables against its own service containers and sees 99.95%.

#### `CACHEX_REQUIRE_LIVE_SERVERS`: where skipping is not acceptable

Opting in protects your data, but it also means a mistyped port or a service
container that never started turns those suites into *skips* — and a skip is
green. Coverage will not catch it: with only the Memcached suite skipped the run
still reports about 96.9%, well over the 90% gate. That threshold cannot be the
backstop here.

So every CI workflow, where the servers are started by the workflow itself and
are therefore guaranteed, also sets `CACHEX_REQUIRE_LIVE_SERVERS=1`.
`tests/test_live_server_gate.py` then turns "these suites would be skipped" into
a failure naming the reason — a missing port, a port with nothing behind it, or
a `redis` package that was never installed.

Keep the two settings separate when you add a workflow: the port says *which*
server is disposable, the flag says *whether* skipping is acceptable at all.
Never set the flag in a shell where the ports point at a server you care about —
it makes the run louder, not safer.

### Strict pytest settings

`[tool.pytest.ini_options]` in `pyproject.toml` keeps the suite strict:

- `asyncio_mode = "auto"`: every `async def` test and fixture runs on
  pytest-asyncio, so tests carry no `@pytest.mark.asyncio`. Write an async test
  instead of calling `asyncio.run()` inside a sync one.
- `--strict-markers`: a mistyped or unregistered marker fails collection
  instead of being silently ignored.
- `xfail_strict = true`: an `xfail` test that starts passing fails the run, so
  the marker gets removed.
- `filterwarnings = ["error"]`: any warning fails the test that raised it. A
  test that expects one asserts it with `pytest.warns(..., match=...)`. A reset
  that only needs the side effect silences that one warning locally, as
  `tests/live_servers.py:flush_memcached` does for `Memcached.clear()`. Add a
  global ignore only for a third-party warning the tests cannot avoid, with a
  comment saying why.

  An unclosed socket counts too: its `ResourceWarning` fails whichever test
  is running when the garbage collector finds it. The autouse
  `close_network_clients` fixture in `tests/conftest.py` closes every Redis
  and Memcached client a test builds, on the test's own event loop. A sync
  test that uses a Redis backend from inside `TestClient` closes it through
  `client.portal`, because the connections belong to that loop.

### Checking that a test can fail

Coverage says a line ran, not that anything checked what it did. A test that
asserts on a value the code never actually produces — `bool(scope["session"])`
where the session data is always empty, or `await get()` returning `None` where
`get()` deletes expired entries itself — passes whether the mechanism works or
not, and trains everyone to ignore it.

The way to find those is to break the thing on purpose and see if the suite
notices:

```bash
# neuter one mechanism (edit a function in fastapi_cachex/ to return early),
# then run the whole suite and restore the source
uv run pytest -q -p no:randomly
git checkout fastapi_cachex/
```

Anything still green is a test that was not testing. A sweep of 25 such
mutations across the backends, the cache decorator and the session layer found
three, including one that claimed to prove a forged `X-Forwarded-For` cannot
satisfy IP binding and would have stayed green with the check disabled entirely.
Worth doing whenever a test is written for something security-relevant.

## Using tox

tox ensures the code works across different Python versions (3.10-3.14, the
`env_list` in `tox.ini`). Each environment installs the `redis` and `memcached`
extras through `tox-uv` and passes the `CACHEX_TEST_*` and
`CACHEX_REQUIRE_LIVE_SERVERS` variables through, so the opt-in rules above apply
unchanged.

tox is for local runs. CI runs the same versions as a job matrix in the
**Test** workflow (`.github/workflows/test.yml`), one job per version, so it
does not run tox as well.

1. Install all Python versions (`uv python install 3.10 3.11 3.12 3.13 3.14`)
2. Run tox:

```bash
uv run tox
```

To run for a specific Python version:

```bash
uv run tox -e py310  # only run for Python 3.10
```

### The lowest supported dependencies

The `py3*` environments install the versions in `uv.lock`, which are the newest
ones. The `lowest` environment instead resolves every direct dependency of the
package, extras included, to the lower bound in `pyproject.toml`
(`uv_resolution = lowest-direct`) and runs the suite on Python 3.10. It is not in
`env_list`; the **Lowest dependencies** workflow runs it with live servers.

```bash
uv run tox -e lowest
```

When you raise or add a lower bound, or start using an API that is newer than
the current floor, run it before pushing.

## Using pre-commit

pre-commit helps maintain code quality by running checks before each commit.

1. pre-commit is part of the `dev` dependency group, so `uv sync --group dev`
   already installs it.

2. Install the git hooks (`.pre-commit-config.yaml` sets
   `default_install_hook_types`, so this installs the `pre-commit`,
   `post-commit` and `post-merge` hooks together):
```bash
uv run pre-commit install
```

3. Run pre-commit manually on all files:
```bash
uv run pre-commit run --all-files
```

The hooks cover the standard pre-commit-hooks checks, `ruff` (with `--fix`) and
`ruff-format`, `typos`, `uv-lock`/`uv-sync`, and strict `mypy` (excluding
`docs/` and `scripts/`). They run automatically on `git commit`. If any checks fail, fix the issues and try committing again.
`uv-lock` only re-resolves the lockfile when `pyproject.toml` changed; it does not
upgrade pinned versions. Upgrades come from Renovate or an explicit
`uv lock --upgrade`.

pre-commit only sees the files you touched. The **Lint** workflow checks the
whole tree; run the same commands before pushing:

```bash
uv run ruff check fastapi_cachex tests scripts
uv run ruff format --check fastapi_cachex tests scripts
uv run mypy fastapi_cachex --strict
uv run mypy tests
uv run mypy scripts
```

## Type Checking with mypy

We use mypy for static type checking to ensure type safety.

1. Run mypy:
```bash
uv run mypy fastapi_cachex
```

2. Run mypy with strict mode (what CI and the release gate run):
```bash
uv run mypy fastapi_cachex --strict
```

### Common mypy Issues

- Make sure all functions have type annotations
- Use `Type | None` for parameters that could be None (the codebase uses PEP 604 unions, not `Optional`)
- Write forward references as quoted annotations (`"SessionManager"`), importing the name under
  `if TYPE_CHECKING:` when it is only needed for typing. Most modules do this; only a couple use
  `from __future__ import annotations`
- Keep `fastapi_cachex/py.typed` in place; it is what makes the installed package typed for users
  (it is listed under `[tool.uv.build-backend] include` in `pyproject.toml`)

## Documentation site

The documentation at <https://fastapi-cachex.readthedocs.io/en/latest/> is built
with [Zensical](https://zensical.org/) from `zensical.toml` and the `docs/`
directory. The home page includes `README.md` and the changelog page includes
`CHANGELOG.md` through snippets, so links in `README.md` must stay absolute
(`https://github.com/allen0099/FastAPI-CacheX/blob/master/...`) to work in both
places.

```bash
uv sync --group docs
uv run zensical serve           # live preview on http://localhost:8000
uv run zensical build --strict  # what CI and Read the Docs run
```

The **Docs** workflow (`.github/workflows/docs.yml`) and Read the Docs
(`.readthedocs.yaml`) both run `zensical build --strict`, so a broken link,
snippet path or docstring reference fails the PR.

A complete app shown in a guide is not copied into the page: it lives in
`examples/`, where `tests/test_examples.py` runs it, and the page includes it
inside a code fence, either the whole file or a named part:

````markdown
```python
;--8<-- "examples/http_cache.py:routes"
```
````

A part is marked in the example file with a start and an end comment (the
syntax is in [`examples/README.md`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/README.md));
the site drops the marker lines. Short fragments that illustrate one call stay inline. The included code keeps
its English comments on the Traditional Chinese site too; explain it in the
text around the fence. `README.md` keeps its quick start inline, since PyPI and
GitHub render it without snippets.

### Traditional Chinese translation

A Traditional Chinese (`zh-TW`) translation is published at
<https://fastapi-cachex.readthedocs.io/zh-tw/latest/>. It is a separate Read the
Docs translation project built from `zensical.zh-TW.toml` (build settings in
`i18n/zh-TW/.readthedocs.yaml`), and its pages live in `i18n/zh-TW/docs/` — not
under `docs/`, which would pull them into the English site.

```bash
uv run zensical serve -f zensical.zh-TW.toml
uv run zensical build --strict -f zensical.zh-TW.toml  # also run by the Docs workflow
```

The English pages are the source of truth. The translation may lag behind
them, and every translated page carries a banner saying so, with a link to the
English original. Pages that are not translated yet are linked from the
translation's navigation to the English site. The API reference (`docs/api/`),
this development guide and the changelog stay English-only (the API pages are
generated from the docstrings, which are in English); the translation links to
their English versions. When translating, follow the
terms in [`i18n/zh-TW/GLOSSARY.md`](https://github.com/allen0099/FastAPI-CacheX/blob/master/i18n/zh-TW/GLOSSARY.md).

### Adding API reference pages

API pages live under `docs/api/` and are generated from docstrings (Google
style) by mkdocstrings. A page is plain markdown with one directive per object:

```markdown
# CacheManager

::: fastapi_cachex.manager.CacheManager
```

Use the full module path where the object is defined (a bare module path such as
`::: fastapi_cachex.types` documents the whole module), and add a new page to the
`nav` in `zensical.toml`. mkdocstrings reads the package statically, so the docs
build does not need the package or its extras installed.

## Releasing

Releasing is manual: run the **Release** workflow from the Actions tab and
choose which part of the version to move (`patch`, `minor` or `major`), or give
it an exact version to override that choice. There is one release path — there
used to be two workflows, `release.yml` for minor releases and `publish.yml`
for patch releases, which meant the part of the version a release moved was
decided by whichever Actions page you happened to open. (`release.yml` also
once ran on a monthly cron, which let the calendar pick the version: whatever
had landed since the last tag went out as the next minor release, whether or
not that was the right number for it.)

The workflow runs in this order:

1. **The gate.** ruff, all three mypy invocations, and the full test suite
   against real Redis and Memcached service containers with
   `CACHEX_REQUIRE_LIVE_SERVERS=1`. A release is the one run where a red test
   result arrives too late to be useful, so it happens before anything is
   written.
2. **The version.** `uv version` applies the bump or the exact version. If
   `vX.Y.Z` is already tagged, locally or on the remote, the run stops here.
3. **The changelog.** `scripts/changelog_release.py` merges the fragments in
   `changelog.d/` into `## [Unreleased]` (see
   [Changelog fragments](#changelog-fragments)), renames it
   to `## [X.Y.Z] - YYYY-MM-DD`, opens a fresh empty `## [Unreleased]` above
   it, rewrites the compare links at the bottom, and writes the release body:
   the notice, if any, each entry's bold summary and issue links, and a link
   to the full entries on the documentation site. The workflow then appends an
   installation snippet and, when there is a previous tag, a compare link.
4. **The build.** `uv build`. The bumped files, the release notes and
   `dist/` are uploaded as one artifact, which the next two jobs download
   instead of building anything again.
5. **The permanent part**, kept together at the end: commit the version bump,
   the promoted changelog and the removal of the merged fragments, push it to master, tag, push the tag by refspec,
   create the GitHub release from the release notes written in step 3,
   publish to PyPI.

Steps 1–4 are the `build` job, which installs every dev dependency and so gets
read access to the repository and nothing else: no git credentials, no PyPI
token. The commit, tag and GitHub release are the `release` job, the only one
that can write to the repository, and it installs nothing. Publishing is the
`publish` job, the only one that can mint a PyPI token, running in the `pypi`
environment so the trusted publisher and any protection rules can be tied to it.

A release runs from `master` only. Dispatched on any other ref, the run fails
at its first step, and the `release` and `publish` jobs check the ref again.

### Rehearsing a release

Dispatch it with **dry run** ticked. Everything in `build` runs — the gate,
the version bump, the tag check, the changelog promotion, `uv build` — and the
`release` and `publish` jobs, which write somewhere permanent (commit, tag,
GitHub release, PyPI), are skipped. Until this existed, the first real exercise
of the release path was a release. A dry run may be dispatched on any branch,
which is how a change to the workflow itself is rehearsed before it is merged.

A dry run answers two questions, and the job summary reports both: whether the
bump and the promotion actually landed in `pyproject.toml`, `uv.lock`,
`CHANGELOG.md` and `changelog.d/` (shown as a `git diff --stat`, staged by
nothing), and what
would have been published. The release notes, the built `dist/` and the bumped
files are attached to the run as an artifact, because the notes are markdown and reading them in
the job summary renders them a second time — which is not what the release page
would show.

Two things a dry run deliberately does not do. It does not pass `--dry-run` to
`scripts/changelog_release.py`: the script's flag prints the body and writes
nothing, while the rehearsal needs the real files, which are then simply never
committed. And it does not publish to TestPyPI — that needs a second set of
credentials and has its own failure modes, so it would be a rehearsal of a
different path, not of this one.

### The changelog is part of the release now

`CHANGELOG.md` used to be maintained entirely by hand and nothing enforced it:
the release notes came from `git log --pretty=format:"- %s (%h)"`, so a release
happened whether or not anyone had written down what it meant. That is no
longer true. The release body is built from the `## [Unreleased]` section,
fragments included, and an empty one fails the run — for entries written by
hand, "nobody wrote it down" is far more likely than "nothing changed". The commit list has not been lost:
the release body ends with a compare link against the previous tag.

The changelog and the release page serve different readers. The changelog
explains each change in full: what behaviour moved, why, and how to adapt. The
release page is scanned, so it gets one line per change. Every entry therefore
opens with a bold summary, and the release body is just those summaries:

```markdown
- **Add `CacheManager.add()` for store-if-absent writes.** It uses the same
  key prefix, JSON encoding and `default_ttl` as `set()`, and runs on the
  backend's atomic `set_if_absent` ... ([#65](https://github.com/allen0099/FastAPI-CacheX/issues/65))
```

becomes ``- Add `CacheManager.add()` for store-if-absent writes. ([#65](...))``
under the same `### Added` heading. The issue links are carried over from
anywhere in the entry, and the script ends the body with a link to the
version's section on the documentation site's changelog page (the workflow
then appends the installation snippet and the compare link). Write the summary for someone
deciding whether this release matters to them: what changed, in the imperative
or as a plain statement, not how. An entry without one fails the run and is
named in the error, and so does a line in the section that is neither a `###`
heading nor a `- ` entry. `tests/test_changelog_release.py` runs the same check
on the real `CHANGELOG.md`, so the pull request that adds an entry without a
summary fails CI instead of the release.

A release can also open with a notice, such as "This is the last 0.3.x
release". Write it into `CHANGELOG.md` by hand, directly under
`## [Unreleased]` and above the first `###` heading or `- ` entry; a fragment
cannot carry one. The notice is copied verbatim to the top of the release body
and stays under the version's heading in `CHANGELOG.md`, so it goes through
review like any other change and remains on record. The fresh
`## [Unreleased]` the release opens has no notice, so each one appears in one
release only. Only that top position counts: a paragraph anywhere else in the
section still fails the run, and a section holding a notice but no entries is
still nothing to release.

Two details of the promotion are worth knowing, because both have bitten this
project:

- The previous version is read from the headings already in the file, never
  derived by subtracting one. 0.3.3 was never released, so `[0.3.4]` compares
  against `v0.3.2`; arithmetic would produce a link to a tag that does not
  exist.
- Only headings and link definitions are rewritten, so a version number
  mentioned in prose — "will be removed in 0.3.5" — is left alone.

To see what a release would publish without changing anything:

```bash
uv run python scripts/changelog_release.py --version 0.3.5 --dry-run
```

**Do not bump the version by hand.** The workflow bumps from whatever
`pyproject.toml` says, so a version edited in advance is bumped *again*: hand
it 0.3.5 and dispatch a patch release, and 0.3.6 goes out with 0.3.5 skipped.
A hand-bump is also what broke the release on 2026-09-05, back when the commit
step treated "nothing to commit" as a failure.

When a pull request changes behaviour, adds public API, or fixes something a
user could have hit, add its entry in the same PR, as a fragment. The release
will fail when there is nothing to release, but it cannot tell you *which* PR
forgot its entry — only that somebody did.

### Changelog fragments

Pull requests do not edit `CHANGELOG.md`. When every PR appended to
`## [Unreleased]`, merging one put every other open PR in conflict. Instead,
each PR adds one file per entry to `changelog.d/`:

- **Name:** `<issue>.<section>.md`, where `<section>` is `added`, `changed`,
  `deprecated`, `removed`, `fixed` or `security`. A second entry for the same
  issue and section is `<issue>.<section>.2.md`, then `.3.md`, and so on; a
  file holds exactly one entry.
- **Content:** the entry, opening with its bold one-line summary, *without*
  the leading `- ` and *without* the issue link. The release adds both, taking
  the link from the file name. Line breaks are up to you; indent a nested list
  by two spaces.

`changelog.d/65.added.md`:

```markdown
**Add `CacheManager.add()` for store-if-absent writes.** It uses the same
key prefix, JSON encoding and `default_ttl` as `set()`.
```

is released under `### Added` as:

```markdown
- **Add `CacheManager.add()` for store-if-absent writes.** It uses the same
  key prefix, JSON encoding and `default_ttl` as `set()`. ([#65](https://github.com/allen0099/FastAPI-CacheX/issues/65))
```

At release time `scripts/changelog_release.py` checks every fragment before it
writes anything, merges them into `## [Unreleased]` — sections in Keep a
Changelog order (Added, Changed, Deprecated, Removed, Fixed, Security), each
after any entry already written there by hand, fragments ordered by issue
number — then promotes the section and deletes the fragments. The `release`
job commits the deletions with `CHANGELOG.md`. `changelog.d/README.md` and
dotfiles are not fragments; any other file there that is not a well-formed
fragment (a bad name, an unknown section, no bold summary, a leading `- `, its
own issue link) fails the release with nothing changed.
`test_the_repository_changelog_can_be_released` merges the pending fragments
the same way, so such a file fails CI in the pull request that adds it.

An entry that belongs to no issue can still be written straight into
`## [Unreleased]` by hand; the release merges it with the fragments.
