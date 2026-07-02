# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FastAPI-CacheX is a Python library (package: `fastapi_cachex`) providing HTTP caching and optional session management for FastAPI. It is published to PyPI as `fastapi-cachex`.

## Commands

All commands use `uv` for environment management.

```bash
# Install development dependencies
uv sync --group dev

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_cache.py

# Run a specific test
uv run pytest tests/test_cache.py::test_function_name

# Run tests with coverage
uv run pytest --cov=fastapi_cachex --cov-report=term-missing

# Lint and format (ruff)
uv run ruff check fastapi_cachex
uv run ruff format fastapi_cachex

# Type checking
uv run mypy fastapi_cachex
uv run mypy fastapi_cachex --strict

# Run pre-commit on all files
uv run pre-commit run --all-files

# Run tox across all Python versions (3.10–3.14)
uv run tox
tox -e py310  # single version
```

## Architecture

### Module Structure

The library has four independent subsystems:

**1. HTTP Caching (`fastapi_cachex/cache.py`, `proxy.py`, `backends/`)**
- `@cache(...)` decorator wraps FastAPI route handlers. It injects a `Request` parameter into the handler signature if not already present, so the handler does not need to declare it.
- Cache flow: check `no-store` → check `no-cache` → check ETag (`If-None-Match`) → check TTL-based cache hit → execute handler → store result.
- Only GET requests are cached; other methods bypass the cache entirely.
- Cache keys follow the format `method|||host|||path|||query_params` (separator defined in `types.py`).
- `BackendProxy` is a non-instantiable class-level singleton (via `ProxyMeta`). Call `BackendProxy.set(backend)` at app startup; `BackendProxy.get()` raises `BackendNotFoundError` if unset. Falls back to `MemoryBackend` automatically inside `@cache` if no backend is set.
- Cache values are stored as `CacheEntry(fingerprint, content, media_type)` dataclass (defined in `types.py`).

**2. Application-Level Caching (`fastapi_cachex/manager.py`, `manager_proxy.py`)**
- `CacheManager` is a thin, JSON-serializing wrapper around whatever backend `BackendProxy` has configured, for caching arbitrary developer values (not HTTP responses) via `get`/`set`/`delete`/`has`/`clear_prefix`/`clear`.
- Keys live under their own `cache:`-prefixed namespace by default (configurable via `key_prefix`), separate from HTTP route keys and `oauth_state:`.
- `get()` never raises — returns `default` (`None` unless overridden) on a miss or decode failure. `set()` lets `TypeError` propagate for non-JSON-serializable values.
- `CacheManagerProxy` mirrors `BackendProxy`/`SessionManagerProxy`. The `AppCache` FastAPI dependency (`get_app_cache`, in `dependencies.py`) lazily creates and registers a default `CacheManager` on first use.
- `clear()`/`clear_prefix()` are built on `backend.get_all_keys()`, so they are no-ops on the Memcached backend (see below).

**3. Session Management (`fastapi_cachex/session/`)**
- Optional subsystem, activated via `SessionMiddleware` and `SessionManagerProxy`.
- `SessionManager` handles create/get/update/delete/invalidate/regenerate operations. It stores `Session` Pydantic models serialized as JSON, wrapped in `CacheEntry` for backend compatibility.
- Token signing: `simple` format uses HMAC-SHA256 (`SecurityManager`); `jwt` format uses PyJWT (optional dependency `fastapi-cachex[jwt]`).
- Session token is passed via custom header (`X-Session-Token` by default) or `Authorization: Bearer` token.
- `SessionManagerProxy` mirrors the `BackendProxy` pattern for managing the `SessionManager` singleton.
- Key FastAPI dependencies: `get_session`, `require_session`, `get_optional_session` (in `session/dependencies.py`).

**4. State Management (`fastapi_cachex/state/`)**
- `StateManager` provides one-time-use state tokens for OAuth flows. States are consumed (deleted) on first successful `consume_state()` call.
- Uses the same cache backends, with key prefix `oauth_state:` by default.

### Backends (`fastapi_cachex/backends/`)

All backends implement `BaseCacheBackend` (abstract base in `backends/base.py`):
- `MemoryBackend`: In-process dict with background cleanup task. Not suitable for multi-process production use.
- `AsyncRedisCacheBackend` (`backends/redis.py`): Fully async; uses `SCAN` (not `KEYS`) for pattern operations. Requires `redis[hiredis]` and `orjson` extras.
- `MemcachedBackend` (`backends/memcached.py`): `clear_pattern`/`get_all_keys` are no-ops (return `0`/`[]` with a `RuntimeWarning`) since the Memcached protocol has no key enumeration. Requires `pymemcache` extra.

Backend keys are namespaced automatically (default prefix: `fastapi_cachex:`).

### Test Setup

`tests/conftest.py` sets `MemoryBackend` as the default backend via an `autouse=True` fixture for every test. Tests requiring Redis or Memcached must configure their own backends. The `memory_backend` fixture manages the cleanup task lifecycle.

### Code Quality Rules

- Ruff is configured with `extend-select = ['ALL']` with specific ignores (see `pyproject.toml`). Notable: E501 (line length), B008 (function calls in defaults), FBT001/FBT002 (boolean args — intentional for Cache-Control API).
- mypy runs in strict mode on the package (not tests).
- pydocstring convention is Google style.
- `from __future__ import annotations` is used for forward references.
- All public functions must have complete type annotations.
- Coverage threshold is 90% (enforced by `pytest-cov`).
