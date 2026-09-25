# FastAPI-Cache X

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml/badge.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml)
[![Coverage Status](https://raw.githubusercontent.com/allen0099/FastAPI-CacheX/coverage-badge/coverage.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/coverage.yml)

[![Downloads](https://static.pepy.tech/badge/fastapi-cachex)](https://pepy.tech/projects/fastapi-cachex)
[![Weekly downloads](https://static.pepy.tech/badge/fastapi-cachex/week)](https://pepy.tech/projects/fastapi-cachex)
[![Monthly downloads](https://static.pepy.tech/badge/fastapi-cachex/month)](https://pepy.tech/projects/fastapi-cachex)

[![PyPI version](https://img.shields.io/pypi/v/fastapi-cachex.svg?logo=pypi&logoColor=gold&label=PyPI)](https://pypi.org/project/fastapi-cachex)
[![Python Versions](https://img.shields.io/pypi/pyversions/fastapi-cachex.svg?logo=python&label=Python&logoColor=gold)](https://pypi.org/project/fastapi-cachex/)

[English](https://fastapi-cachex.readthedocs.io/en/latest/) | [繁體中文](https://fastapi-cachex.readthedocs.io/zh-tw/latest/)

A high-performance caching extension for FastAPI: a server-side response cache with `Cache-Control` and `ETag` support, application-level caching, and optional session management.

**Documentation:** <https://fastapi-cachex.readthedocs.io/en/latest/> — guides and the full API reference.

## Features

- **HTTP caching** — a `@cache` decorator for GET routes with `Cache-Control`,
  `ETag` / `If-None-Match` (304) and per-route invalidation.
- **Application cache** — `CacheManager` for caching arbitrary JSON values in
  your own code, with compute-on-miss `get_or_set()` and atomic store-if-absent `add()`.
- **Backends** — in-memory, Redis and Memcached, with atomic counters,
  one-shot values and locks.
- **Sessions (optional)** — HMAC-signed or JWT session tokens over headers,
  bearer tokens or cookies, with sliding expiration and IP/User-Agent binding.
- **OAuth state** — one-time state tokens for CSRF protection in OAuth/OIDC flows.

## Installation

```bash
uv add fastapi-cachex
```

Everything in the core package works with the in-memory backend. The other
backends and the optional session transports ship as extras:

| Extra | Install | Pulls in | Needed for |
|-------|---------|----------|------------|
| `redis` | `uv add "fastapi-cachex[redis]"` | `redis[hiredis]`, `orjson` | `AsyncRedisCacheBackend` |
| `memcache` | `uv add "fastapi-cachex[memcache]"` | `pymemcache` | `MemcachedBackend` (note: `memcache`, not `memcached`) |
| `jwt` | `uv add "fastapi-cachex[jwt]"` | `PyJWT` | `SessionConfig(token_format="jwt")` |

Extras combine: `uv add "fastapi-cachex[redis,jwt]"`.

## Quick Start

```python
from fastapi import FastAPI

from fastapi_cachex import AppCache, BackendProxy, cache
from fastapi_cachex.backends import MemoryBackend

app = FastAPI()
BackendProxy.set(MemoryBackend())  # or AsyncRedisCacheBackend / MemcachedBackend


@app.get("/items/{item_id}")
@cache(ttl=60)  # served from the cache for 60 seconds, with ETag revalidation
async def read_item(item_id: int):
    return {"item_id": item_id}


def build_report() -> dict:
    return {"total": 42}  # stands in for something slow


@app.get("/report")
async def report(cache: AppCache):
    # Cache any JSON value in your own code.
    return await cache.get_or_set("report", build_report, ttl=300)
```

> [!WARNING]
> The default cache key carries no user identity. Cache authenticated endpoints
> with `private=True` or a per-user key builder — see
> [Authenticated endpoints](https://fastapi-cachex.readthedocs.io/en/latest/HTTP_CACHING/#authenticated-endpoints).

## Documentation

- [HTTP caching](https://fastapi-cachex.readthedocs.io/en/latest/HTTP_CACHING/) — the `@cache` decorator, Cache-Control directives, cache keys, invalidation and monitoring routes
- [Cache flow](https://fastapi-cachex.readthedocs.io/en/latest/CACHE_FLOW/) — what happens inside a cached request
- [Application cache](https://fastapi-cachex.readthedocs.io/en/latest/APP_CACHE/) — `CacheManager`
- [Backends](https://fastapi-cachex.readthedocs.io/en/latest/BACKENDS/) — choosing and configuring a backend, atomic primitives
- [Session management](https://fastapi-cachex.readthedocs.io/en/latest/SESSION/) and [JWT claims](https://fastapi-cachex.readthedocs.io/en/latest/JWT_CLAIMS/)
- [OAuth state](https://fastapi-cachex.readthedocs.io/en/latest/STATE/) — one-shot OAuth/CSRF state tokens
- [Distributed lock](https://fastapi-cachex.readthedocs.io/en/latest/LOCK/) — `CacheLock` for multi-process mutual exclusion
- [API reference](https://fastapi-cachex.readthedocs.io/en/latest/api/http-caching/)
- [Development guide](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/) and [contributing](https://fastapi-cachex.readthedocs.io/en/latest/CONTRIBUTING/)
- [Changelog](https://github.com/allen0099/FastAPI-CacheX/blob/master/CHANGELOG.md) · [Known limitations and planned work](https://github.com/allen0099/FastAPI-CacheX/issues)

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](https://github.com/allen0099/FastAPI-CacheX/blob/master/LICENSE) file for details.
