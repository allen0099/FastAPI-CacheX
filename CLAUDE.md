# CLAUDE.md

Guidance for Claude Code in this repository. It lists only what the code, `pyproject.toml` and the docs do not make obvious. For the rest, read the source (module docstrings are thorough), `docs/` and `docs/DEVELOPMENT.md`.

## Project

`fastapi_cachex` (PyPI `fastapi-cachex`): HTTP caching, application cache and a distributed lock for FastAPI, on pluggable backends (memory, Redis, Memcached). The 0.4 line is current (0.4.0 released 2026-10-02). `release.yml` releases only from `master`, so `master` stays 0.4.x-compatible: merging breaking 0.5.0 work (milestone `0.5.0`) ends 0.4.x patch releases. Sessions and OAuth state (`fastapi_cachex.session`, `fastapi_cachex.state`) are deprecated (#420) and get security fixes only until 0.5.0 removes them (#421).

## Commands

Use `uv` for everything (`uv sync --group dev --all-extras`, `uv run ...`).

- `uv run pytest` does not check coverage. The 90% floor (`fail_under`) applies only with `--cov=fastapi_cachex`.
- Redis and Memcached tests are skipped unless `CACHEX_TEST_REDIS_PORT` / `CACHEX_TEST_MEMCACHED_PORT` are set. They flush the server they connect to, so point them only at a throwaway instance (`scripts/start-*-server.sh`).
- `tox -e lowest` runs every direct dependency at its declared floor on Python 3.10.
- Docs need `uv sync --group dev --group docs --all-extras`, then `uv run zensical build --strict -f zensical.toml` (and `-f zensical.zh-TW.toml`).
- Before committing, `git add -A` and then run `uv run pre-commit run --all-files`. ruff-format rewrites files, so re-add and re-run until it passes. CI also runs `mypy tests` and `mypy scripts`, which pre-commit does not.

## Invariants that span modules

- HTTP cache keys are `http:v2|method|host|path|query` (`HTTP_KEY_FORMAT_TAG`, `CACHE_KEY_SEPARATOR = "|"`), built and parsed by `CacheKey` in `cache_key.py`. Method, host, path and extra components go through `escape_key_component` so client input cannot inject the separator; a query over 200 bytes becomes `sha256:<hex>`. A format change bumps the tag. Anything that builds or parses keys (`clear_path`, `routes.py`, backends) must go through `CacheKey`.
- `@cache` fails open by default: a backend error is logged and treated as a miss, or the response is served unstored. Only GET is cached.
- Backend lookup: `@cache` and the `CacheBackend` / `AppCache` dependencies fall back to a `MemoryBackend` when none is set. `CacheLock`, `StateManager` and a directly built `CacheManager(...)` call `BackendProxy.get()` and raise; the monitoring routes and `invalidate()` treat a missing backend as empty. The proxies' lazy creation goes through `ProxyBase.get_or_create` (per-class lock; sync dependencies run in threads).
- Memcached cannot enumerate keys. `clear_pattern`, `get_all_keys` and every `CacheManager.clear*` are no-ops with a `RuntimeWarning`, while `MemcachedBackend.clear()` issues `flush_all` and wipes the whole server.
- The atomic primitives on `BaseCacheBackend` (`increment`, `get_and_delete`, `set_if_absent`, `delete_if_equals`, `expire_if_equals`, `delete_many`) have non-atomic fallbacks. Every built-in backend must override them atomically; see "Atomic backend primitives" in `docs/BACKENDS.md`. `tests/backends/*_contract.py` covers TTL validation, counters and `clear_pattern`; the other primitives are tested in each backend's own test file, so a change needs all three.
- `validate_ttl` / `validate_delta` run before any I/O in every backend; floats and bools raise `TypeError`.
- Redis and Memcached keys carry `key_prefix` (default `fastapi_cachex:`); memory has no prefix. `CacheManager` (`cache:`), `StateManager` (`oauth_state:`) and `CacheLock` (`lock:`) add their own prefixes on top.

## Tests

- `filterwarnings = ["error"]`: an expected warning needs `pytest.warns`, or a local `warnings.catch_warnings` (see `flush_memcached` in `tests/live_servers.py`).
- `tests/conftest.py` sets a `MemoryBackend` for every test and resets the proxies. Tests for Redis or Memcached build their own backend. The autouse `close_network_clients` fixture closes every Redis/Memcached client, because an unclosed socket's `ResourceWarning` would fail a later test.
- `examples/*.py` are tested by `tests/test_examples.py` and included in the docs with `--8<--` snippets, so editing an example changes the docs.
- Check that a new test can fail: break the code it guards and confirm the test fails (see `docs/DEVELOPMENT.md`).

## Conventions

- Changelog: add a `changelog.d/<issue>.<section>.md` fragment and never edit `CHANGELOG.md`. The format is in `changelog.d/README.md`.
- Docs changes go into both `docs/` and `i18n/zh-TW/docs/`. Terms follow `i18n/zh-TW/GLOSSARY.md`.
- Never edit `version` in `pyproject.toml` by hand. Releases run through the `release.yml` workflow (`docs/DEVELOPMENT.md#releasing`).
- Breaking changes need a runtime warning in a release first and land only in the next minor, with a section in that minor's migration guide (`docs/MIGRATING_0_4.md` for 0.4.0; 0.5.0 gets a new `docs/MIGRATING_0_5.md` with its zh-TW copy and nav entry).
