# Contributing to FastAPI-CacheX

We love your input! We want to make contributing to FastAPI-CacheX as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features
- Becoming a maintainer

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
2. Add an entry to the `## [Unreleased]` section of
   [CHANGELOG.md](https://github.com/allen0099/FastAPI-CacheX/blob/master/CHANGELOG.md) if your change alters behaviour, adds public
   API, or fixes something a user could have hit. Open the entry with a bold
   one-line summary, `- **What changed.** The details...`: the release notes
   list only those summaries, and CI fails on an entry without one. A release
   also refuses to run on an empty section, so an omission surfaces — but only
   at release time, and only as "somebody forgot", never as which PR it was
3. Update the documentation with any new dependencies, features, or changes.
   New public API needs a docstring and, if it lives in a module not yet
   covered, an entry under `docs/api/`; check the site with
   `uv run zensical build --strict` (see
   [Documentation site](DEVELOPMENT.md#documentation-site))
4. The PR may be merged once you have the sign-off of at least one other developer

## Any Questions?

Feel free to open an issue with the `question` label if you need any help!
