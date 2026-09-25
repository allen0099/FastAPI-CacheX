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
at about 92.2%, but only because the rest of the suite carries it — `redis.py`
alone drops to roughly 27%. The margin is thin, so the first place an untested
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

### Checking that a test can fail

Coverage says a line ran, not that anything checked what it did. A test that
asserts on a value the code never actually produces — `bool(scope["session"])`
where the session data is always empty, or `await get()` returning `None` where
`get()` deletes expired entries itself — passes whether the mechanism works or
not, and trains everyone to ignore it.

The way to find those is to break the thing on purpose and see if the suite
notices:

```bash
# neuter one mechanism, then run the whole suite
git stash -- fastapi_cachex/         # or edit the function to return early
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
`env_list` in `tox.ini`). Each environment installs the `redis` and `memcache`
extras through `tox-uv` and passes the `CACHEX_TEST_*` and
`CACHEX_REQUIRE_LIVE_SERVERS` variables through, so the opt-in rules above apply
unchanged.

1. Install all Python versions (`uv python install 3.10 3.11 3.12 3.13 3.14`)
2. Run tox:

```bash
uv run tox
```

To run for a specific Python version:

```bash
uv run tox -e py310  # only run for Python 3.10
```

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
translation's navigation to the English site. When translating, follow the
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
3. **The changelog.** `scripts/changelog_release.py` renames `## [Unreleased]`
   to `## [X.Y.Z] - YYYY-MM-DD`, opens a fresh empty `## [Unreleased]` above
   it, rewrites the compare links at the bottom, and writes the promoted
   section out to be used as the release body.
4. **The permanent part**, kept together at the end: commit the version bump
   and the promoted changelog, push it, tag, push the tag by refspec, create
   the GitHub release from the promoted section, publish to PyPI.

### Rehearsing a release

Dispatch it with **dry run** ticked. Everything runs — the gate, the version
bump, the tag check, the changelog promotion, `uv build` — and the four steps
that write somewhere permanent (commit, tag, GitHub release, PyPI) are skipped.
Until this existed, the first real exercise of the release path was a release.

A dry run answers two questions, and the job summary reports both: whether the
bump and the promotion actually landed in `pyproject.toml`, `uv.lock` and
`CHANGELOG.md` (shown as a `git diff --stat`, staged by nothing), and what
would have been published. The release notes and the built `dist/` are attached
to the run as an artifact, because the notes are markdown and reading them in
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
longer true. The release body **is** the `## [Unreleased]` section, and an
empty one fails the run — for a hand-maintained file, "nobody wrote it down" is
far more likely than "nothing changed". The commit list has not been lost: the
release body ends with a compare link against the previous tag.

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
user could have hit, add the entry to `## [Unreleased]` in the same PR. The
release will fail on an empty section, but it cannot tell you *which* PR forgot
its entry — only that somebody did.
