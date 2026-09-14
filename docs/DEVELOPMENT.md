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

Note that the coverage gate (`fail_under = 90`) is only met with both services
opted in; a skipped run leaves the network backends uncovered. CI sets both
variables against its own service containers.

## Using tox

tox ensures the code works across different Python versions (3.10-3.13).

1. Install all Python versions
2. Run tox:

```bash
uv run tox
```

To run for a specific Python version:

```bash
tox -e py310  # only run for Python 3.10
```

## Using pre-commit

pre-commit helps maintain code quality by running checks before each commit.

1. Install pre-commit:
```bash
uv add --dev pre-commit
```

2. Install the pre-commit hooks:
```bash
uv run pre-commit install
```

3. Run pre-commit manually on all files:
```bash
uv run pre-commit run --all-files
```

The pre-commit hooks will automatically run on `git commit`. If any checks fail, fix the issues and try committing again.

## Type Checking with mypy

We use mypy for static type checking to ensure type safety.

1. Run mypy:
```bash
uv run mypy fastapi_cachex
```

2. Run mypy with strict mode:
```bash
uv run mypy fastapi_cachex --strict
```

### Common mypy Issues

- Make sure all functions have type annotations
- Use `Optional[Type]` for parameters that could be None
- Use `from __future__ import annotations` for forward references
- Add `py.typed` file to make your package mypy compliant
