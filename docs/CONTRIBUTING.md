# Contributing to FastAPI-CacheX

We love your input! We want to make contributing to FastAPI-CacheX as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features
- Becoming a maintainer

## Reporting a Security Vulnerability

Please do not report security problems in public issues or pull requests.
Report them privately through
[GitHub private vulnerability reporting](https://github.com/allen0099/FastAPI-CacheX/security/advisories/new)
instead. The [security policy](https://github.com/allen0099/FastAPI-CacheX/blob/master/SECURITY.md)
lists the supported versions and what to include in a report.

## Before You Open a Pull Request

Pull requests from outside contributors start from an issue:

1. Find or open an issue for the change, and say on it that you would like to
   work on it.
2. Wait until a maintainer assigns the issue to you.
3. Open the pull request with `Fixes #<issue>` in its description.

A change under `fastapi_cachex/` also needs a test under `tests/` and a
changelog fragment (see [Pull Request Process](#pull-request-process)).

A check, **PR gate**, enforces this for outside contributors; maintainers,
collaborators and bots such as Renovate are exempt. It closes a pull request
that does not close an issue assigned to its author. Editing a closed pull
request does not reopen it, so open a new one once the issue is assigned to
you. When only the tests or the fragment are missing, the pull request stays
open, the check fails with a comment listing what is missing, and it runs
again on every push or edit. The check asks for a fragment on every change
under `fastapi_cachex/`; when a change needs none, such as a refactor, a
maintainer waives the check with the `skip-pr-gate` label. A maintainer who
reopens a pull request the check closed adds that label first, or the check
closes it again.

## Development Process

1. Fork the project
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## Development Setup

Please refer to our [Development Guide](DEVELOPMENT.md) for detailed instructions on setting up your development environment.

> [!WARNING]
> The Redis and Memcached test suites wipe the server they connect to, so they
> skip unless you name a port with `CACHEX_TEST_REDIS_PORT` /
> `CACHEX_TEST_MEMCACHED_PORT`. Point them at a throwaway container, never at a
> server whose data you want to keep — see
> [Redis and Memcached tests are opt-in](DEVELOPMENT.md#redis-and-memcached-tests-are-opt-in).

## Pull Request Process

1. Update the matching guide under `docs/` (and the README if the change belongs on the front page) when you change the interface. Only the English
   pages need updating: the [Traditional Chinese translation](DEVELOPMENT.md#traditional-chinese-translation)
   is allowed to lag behind them
2. Add a changelog fragment if your change alters behaviour, adds public
   API, or fixes something a user could have hit: a file
   `changelog.d/<issue>.<section>.md` (section `added`, `changed`,
   `deprecated`, `removed`, `fixed` or `security`) holding the entry, opening
   with a bold one-line summary, `**What changed.** The details...`, without a
   leading `- ` or the issue link — the release adds both. Do not edit
   [CHANGELOG.md](https://github.com/allen0099/FastAPI-CacheX/blob/master/CHANGELOG.md)
   directly; see [Changelog fragments](DEVELOPMENT.md#changelog-fragments). The
   release notes list only the summaries, and CI fails on a malformed fragment.
   A release also refuses to run with nothing to release, so an omission
   surfaces — but only at release time, and only as "somebody forgot", never as
   which PR it was
3. Update the documentation with any new dependencies, features, or changes.
   New public API needs a docstring and, if it lives in a module not yet
   covered, an entry under `docs/api/`; check the site with
   `uv run zensical build --strict` (see
   [Documentation site](DEVELOPMENT.md#documentation-site))
4. The PR may be merged once you have the sign-off of at least one other developer

## Any Questions?

Feel free to open an issue with the `question` label if you need any help!
