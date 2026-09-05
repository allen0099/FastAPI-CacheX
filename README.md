# FastAPI-Cache X

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml/badge.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml)
[![Coverage Status](https://raw.githubusercontent.com/allen0099/FastAPI-CacheX/coverage-badge/coverage.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/coverage.yml)

[![Downloads](https://static.pepy.tech/badge/fastapi-cachex)](https://pepy.tech/project/fastapi-cachex)
[![Weekly downloads](https://static.pepy.tech/badge/fastapi-cachex/week)](https://pepy.tech/project/fastapi-cachex)
[![Monthly downloads](https://static.pepy.tech/badge/fastapi-cachex/month)](https://pepy.tech/project/fastapi-cachex)

[![PyPI version](https://img.shields.io/pypi/v/fastapi-cachex.svg?logo=pypi&logoColor=gold&label=PyPI)](https://pypi.org/project/fastapi-cachex)
[![Python Versions](https://img.shields.io/pypi/pyversions/fastapi-cachex.svg?logo=python&label=Python&logoColor=gold)](https://pypi.org/project/fastapi-cachex/)

[English](README.md) | [繁體中文](docs/README.zh-TW.md)

A high-performance caching extension for FastAPI, providing comprehensive HTTP caching support and optional session management.

## Features

### HTTP Caching
- Support for HTTP caching headers
    - `Cache-Control`
    - `ETag`
    - `If-None-Match`
- Multiple backend cache support
    - Redis
    - Memcached
    - In-memory cache
- Complete Cache-Control directive implementation
- Easy-to-use `@cache` decorator

### Session Management (Optional Extension)
- Secure session management with HMAC-SHA256 token signing
- Optional JWT token format for interoperability (install extra `jwt`)
- IP address and User-Agent binding (optional security features)
- Header and bearer token support (API-first architecture)
- Automatic session renewal (sliding expiration)
- Flash messages for cross-request communication
- Multiple backend support (Redis, Memcached, In-Memory)
- Complete session lifecycle management (create, validate, refresh, invalidate)

### Cache-Control Directives

| Directive                | Supported          | Description                                                                                             |
|--------------------------|--------------------|---------------------------------------------------------------------------------------------------------|
| `max-age`                | :white_check_mark: | Specifies the maximum amount of time a resource is considered fresh.                                    |
| `s-maxage`               | :x:                | Specifies the maximum amount of time a resource is considered fresh for shared caches.                  |
| `no-cache`               | :white_check_mark: | Forces caches to submit the request to the origin server for validation before releasing a cached copy. |
| `no-store`               | :white_check_mark: | Instructs caches not to store any part of the request or response.                                      |
| `no-transform`           | :x:                | Instructs caches not to transform the response content.                                                 |
| `must-revalidate`        | :white_check_mark: | Forces caches to revalidate the response with the origin server after it becomes stale.                 |
| `proxy-revalidate`       | :x:                | Similar to `must-revalidate`, but only for shared caches.                                               |
| `must-understand`        | :x:                | Indicates that the recipient must understand the directive or treat it as an error.                     |
| `private`                | :white_check_mark: | Indicates that the response is intended for a single user and should not be stored by shared caches.    |
| `public`                 | :white_check_mark: | Indicates that the response may be cached by any cache, even if it is normally non-cacheable.           |
| `immutable`              | :white_check_mark: | Indicates that the response body will not change over time, allowing for longer caching.                |
| `stale-while-revalidate` | :white_check_mark: | Indicates that a cache can serve a stale response while it revalidates the response in the background.  |
| `stale-if-error`         | :white_check_mark: | Indicates that a cache can serve a stale response if the origin server is unavailable.                  |

## Installation

```bash
uv add fastapi-cachex
```

To enable JWT token format support for sessions:

```bash
uv add "fastapi-cachex[jwt]"
```

### Development Installation

```bash
uv add git+https://github.com/allen0099/FastAPI-CacheX.git
```

## Quick Start

```python
from fastapi import FastAPI
from fastapi_cachex import cache
from fastapi_cachex import CacheBackend

app = FastAPI()


@app.get("/")
@cache(ttl=60)  # Cache for 60 seconds
async def read_root():
    return {"Hello": "World"}


@app.get("/no-cache")
@cache(no_cache=True)  # Mark this endpoint as non-cacheable
async def non_cache_endpoint():
    return {"Hello": "World"}


@app.get("/no-store")
@cache(no_store=True)  # Mark this endpoint as non-cacheable
async def non_store_endpoint():
    return {"Hello": "World"}


@app.get("/clear_cache")
async def remove_cache(cache: CacheBackend):
    await cache.clear_path("/path/to/clear")  # Clear cache for a specific path
    await cache.clear_pattern("/path/to/clear/*")  # Clear cache for a specific pattern
```

### Application-Level Caching (Manual Get/Set)

Beyond HTTP response caching via `@cache`, you can cache arbitrary JSON-serializable
Python values directly in your business logic using `CacheManager`. It's a thin,
namespaced wrapper around whichever backend is configured via `BackendProxy`.

```python
from fastapi_cachex import AppCache, CacheManager

@app.get("/expensive")
async def expensive_operation(cache: AppCache):
    result = await cache.get("expensive:result")
    if result is None:
        result = perform_expensive_calculation()
        await cache.set("expensive:result", result, ttl=300)
    return result


# Or instantiate directly, e.g. outside of a request:
manager = CacheManager(key_prefix="myapp:", default_ttl=60)
await manager.set("user:42", {"name": "Alice"})
user = await manager.get("user:42")  # {"name": "Alice"}
await manager.delete("user:42")
await manager.clear_prefix()  # clear everything under "myapp:"
```

`CacheManager.get()` returns `None` (or a supplied `default=`) on a cache miss —
it never raises for missing or corrupted entries. `CacheManager` keys live under
their own `cache:`-prefixed namespace by default, separate from the HTTP route
cache and OAuth state, so `clear()`/`clear_prefix()` never touch unrelated cache
entries.

**Note**: `clear()`/`clear_prefix()` are implemented via the backend's
`get_all_keys()` and `delete_many()` (one batched `DEL` on Redis). Since Memcached doesn't support key enumeration (see
[Memcached limitations](#memcached)), these two methods are no-ops on a
Memcached backend — `get()`/`set()`/`delete()`/`has()` work normally. Use
Redis or the in-memory backend if you need bulk clearing.

## Backend Configuration

FastAPI-CacheX supports multiple caching backends. You can easily switch between them using the `BackendProxy`.

### Cache Key Format

Cache keys are generated in the following format to avoid collisions:

```
{method}|||{host}|||{path}|||{query_params}
```

This ensures that:
- Different HTTP methods (GET, POST, etc.) don't share cache
- Different hosts don't share cache (useful for multi-tenant scenarios)
- Different query parameters get separate cache entries
- The same endpoint with different parameters can be cached independently

All backends automatically namespace keys with a prefix (e.g., `fastapi_cachex:`) to avoid conflicts with other applications.

`CacheManager` (see [Application-Level Caching](#application-level-caching-manual-getset)) uses a separate, simpler `cache:`-prefixed key namespace instead of this `|||`-separated format, since its keys aren't tied to HTTP requests.

### Cache Hit Behavior

When a cached entry is valid (within TTL):
- **Default behavior**: Returns the cached content with HTTP 200 status code directly without re-executing the endpoint handler
- **With `If-None-Match` header**: Returns HTTP 304 Not Modified if the ETag matches
- **With `no-cache` directive**: Forces revalidation with fresh content before deciding on 304

This means **cached hits are extremely fast** - the endpoint handler function is never executed.

### Atomic backend primitives

Every backend exposes two atomic operations on top of `get`/`set`/`delete`, for
values that are read and written by many concurrent requests:

```python
from fastapi_cachex import BackendProxy

backend = BackendProxy.get()

# Fixed-window counter: created on first use, `ttl` applies only then.
hits = await backend.increment(f"resend:{user_id}", ttl=86400)
if hits > 3:
    raise TooManyRequests()

# One-shot value: of several concurrent callers exactly one gets the entry.
grant = await backend.get_and_delete(f"grant:{token}")
```

- `increment(key, delta=1, ttl=None) -> int` — Memory does the read-modify-write
  under its lock, Redis runs a Lua script (`EXISTS` + `INCRBY` + `EXPIRE`) and
  Memcached uses `ADD` + `INCR`/`DECR` (Memcached counters stop at 0). The
  counter is visible through `get()` as a `CacheEntry` with fingerprint
  `COUNTER_FINGERPRINT` and the decimal value as content, so `delete`/`clear*`
  and the monitoring routes treat it like any other entry. Incrementing a key
  that holds a cached response raises `CacheXError`.
- `get_and_delete(key) -> CacheEntry | None` — Memory pops under its lock, Redis
  uses `GETDEL` (server 6.2+) and Memcached returns the value only when its own
  `DELETE` won. `StateManager.consume_state`, `CacheManager.delete` and
  `invalidate()` are built on it.

Both have a non-atomic fallback on `BaseCacheBackend`, so a third-party backend
that only implements the abstract methods keeps working; override them to get
real atomicity.

### In-Memory Cache (default)

If you don't specify a backend, FastAPI-CacheX will use the in-memory cache by default.
This is suitable for development and testing purposes. The backend automatically runs
a cleanup task to remove expired entries every 60 seconds.

```python
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex import BackendProxy

backend = MemoryBackend()
BackendProxy.set(backend)
```

**Note**: In-memory cache is not suitable for production with multiple processes.
Each process maintains its own separate cache.

### Memcached

```python
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex import BackendProxy

backend = MemcachedBackend(servers=["localhost:11211"])
BackendProxy.set(backend)
```

**Limitations**:
- Pattern-based key clearing (`clear_pattern`) is not supported by the Memcached protocol
- Keys are namespaced with `fastapi_cachex:` prefix to avoid conflicts
- Consider using Redis backend if you need pattern-based cache clearing

The synchronous pymemcache client runs in worker threads and is connection-pooled,
so concurrent requests never share a socket. Writes wait for the server's
acknowledgement (`default_noreply=False`), which keeps a value readable from
any pooled connection as soon as `set()` returns.

### Redis

```python
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex import BackendProxy

backend = AsyncRedisCacheBackend(host="127.0.0.1", port=6379, db=0)
BackendProxy.set(backend)
```

**Features**:
- Fully async implementation
- Supports pattern-based key clearing
- Uses SCAN instead of KEYS for safe production use (non-blocking)
- Namespaced with `fastapi_cachex:` prefix by default
- Optional custom key prefix for multi-tenant scenarios

**Example with custom prefix**:

```python
backend = AsyncRedisCacheBackend(
    host="127.0.0.1",
    port=6379,
    key_prefix="myapp:cache:",
)
BackendProxy.set(backend)
```

## Performance Considerations

### Cache Hit Performance

When a cache hit occurs (within TTL), the response is returned directly without executing your endpoint handler. This is extremely fast:

```python
@app.get("/expensive")
@cache(ttl=3600)  # Cache for 1 hour
async def expensive_operation():
    # This is ONLY executed when cache misses
    # On cache hits, this function is never called
    result = perform_expensive_calculation()
    return result
```

### Backend Selection

- **MemoryBackend**: Fastest for single-process development; not suitable for production
- **Memcached**: Good for distributed systems; has limitations on pattern clearing
- **Redis**: Best for production; fully async, supports all features, non-blocking operations

## Documentation

- [Cache Flow Explanation](docs/CACHE_FLOW.md)
- [Development Guide](docs/DEVELOPMENT.md)
- [Contributing Guidelines](docs/CONTRIBUTING.md)
- [Session Management Guide](docs/SESSION.md) - Complete guide for session features

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
