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

[English](https://github.com/allen0099/FastAPI-CacheX/blob/master/README.md) | [繁體中文](https://github.com/allen0099/FastAPI-CacheX/blob/master/docs/README.zh-TW.md)

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
- Header, bearer token and cookie transports (cookies via
  `FastAPICacheXSessionMiddleware`; the older `SessionMiddleware` is deprecated
  and removed in 0.4.0 — see [Session Management Guide](https://github.com/allen0099/FastAPI-CacheX/blob/master/docs/SESSION.md))
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

Everything in the core package works with the in-memory backend. The other
backends and the optional session transports ship as extras:

| Extra | Install | Pulls in | Needed for |
|-------|---------|----------|------------|
| `redis` | `uv add "fastapi-cachex[redis]"` | `redis[hiredis]`, `orjson` | `AsyncRedisCacheBackend` |
| `memcache` | `uv add "fastapi-cachex[memcache]"` | `pymemcache` | `MemcachedBackend` (note: `memcache`, not `memcached`) |
| `jwt` | `uv add "fastapi-cachex[jwt]"` | `PyJWT` | `SessionConfig(token_format="jwt")` |

The `starlette` extra is gone: `itsdangerous` is a base dependency now, so
`FastAPICacheXSessionMiddleware` works on a plain install.

Extras combine: `uv add "fastapi-cachex[redis,jwt]"`.

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
    # Patterns match the whole key, not just the path
    await cache.clear_pattern("GET|||*|||/path/to/clear/*")
```

`clear_pattern` globs the whole logical key. HTTP cache keys look like
`method|||host|||path|||query`, so a pattern that is only a path matches
nothing — use `clear_path(path, include_params=True)` when the path is what you
mean, and keep `clear_pattern` for keys you built yourself.

### Invalidating a Single Cached Route

`clear_path`/`clear_pattern` work on ranges of keys. To drop exactly the entry a
`@cache`-decorated route would use — typically right after a mutation — call
`invalidate()`, which rebuilds that route's key with the same key builder and
deletes it:

```python
from fastapi import Request
from starlette.requests import Request as StarletteRequest

from fastapi_cachex import cache, invalidate


@app.get("/items/{item_id}")
@cache(ttl=300)
async def read_item(item_id: int):
    return await load(item_id)


@app.post("/items/{item_id}")
async def update_item(item_id: int, request: Request):
    await save(item_id)
    # Build the key the cached GET would have used: same host and headers,
    # GET method, the cached path, no query string.
    scope = dict(request.scope)
    scope["method"] = "GET"
    scope["path"] = f"/items/{item_id}"
    scope["query_string"] = b""
    return {"invalidated": await invalidate(StarletteRequest(scope))}
```

`invalidate(request, key_builder=None)` returns `True` when an entry existed and
was removed, `False` otherwise (including when no backend is configured — it
never raises). The request you hand it must produce the cached route's key:
same method, host, path and query string. If the cached route uses a custom
`key_builder`, pass the same one here, or the key will not match.

### Cache Monitoring Routes

`add_routes()` mounts two read-only endpoints that report what is currently in
the backend:

```python
from fastapi import Depends, FastAPI
from fastapi_cachex import add_routes

app = FastAPI()
add_routes(
    app,
    prefix="/admin/cache",  # default "" -> /cached-hits, /cached-records
    include_in_schema=False,  # default: hidden from OpenAPI
    dependencies=[Depends(verify_admin)],
)
```

- `GET {prefix}/cached-hits` — per-route hit counts and cache key information.
- `GET {prefix}/cached-records` — every cached record with its size, expiry and
  a preview of the cached content.

> [!WARNING]
> **These routes have no authentication of their own.** `include_in_schema=False`
> only hides them from the OpenAPI document; anyone who guesses the path can read
> them. `/cached-records` includes a preview of the cached content and exposes
> your whole route structure. In production always pass
> `dependencies=[Depends(your_auth)]`, or mount them on an internal-only app.

> [!NOTE]
> The `ttl_remaining` field is not available on the Redis backend.
> `AsyncRedisCacheBackend.get_cache_data()` does not issue a per-key `TTL`
> lookup, so Redis-backed entries are reported as never expiring. Expiry itself
> still happens — only the monitoring view is blind to it.

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

# Compute-on-miss: `factory` runs only when the key is missing, expired, or
# undecodable. It may be sync or async.
profile = await manager.get_or_set("user:42", lambda: load_user(42), ttl=300)

# Glob over this manager's namespace, using the backend's native pattern
# support (Redis SCAN) rather than enumerating every key.
await manager.clear_pattern("user:*")  # matches "myapp:user:*"
```

`get_or_set()` provides no stampede protection: concurrent misses for the same
key each run `factory`. `CacheManager.get()` returns `None` (or a supplied `default=`) on a cache miss —
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

Query parameters are taken in the order the client sent them, without sorting, so
`?a=1&b=2` and `?b=2&a=1` are two distinct cache entries for the same logical request.

All backends automatically namespace keys with a prefix (e.g., `fastapi_cachex:`) to avoid conflicts with other applications.

`CacheManager` (see [Application-Level Caching](#application-level-caching-manual-getset)) uses a separate, simpler `cache:`-prefixed key namespace instead of this `|||`-separated format, since its keys aren't tied to HTTP requests.

> [!WARNING]
> **The default cache key carries no user identity.** The backend is shared by
> every worker and every caller, so caching an authenticated endpoint with the
> default key builder will serve one user's response to the next user who hits
> the same path.
>
> For any endpoint whose response depends on who is asking, do one of:
>
> 1. **`private=True`** — the response is never read from or written to the
>    shared backend. `Cache-Control: private` still lets the user's own browser
>    cache it, and `If-None-Match` revalidation still works against freshly
>    rendered content.
> 2. **A key builder that includes the caller's identity** — use this when you
>    do want a server-side cache per user.

```python
from fastapi_cachex import cache
from fastapi_cachex.types import CACHE_KEY_SEPARATOR


# 1. Keep it out of the shared cache entirely.
@app.get("/me/profile")
@cache(ttl=60, private=True)
async def my_profile(user: CurrentUser):
    return user.profile


# 2. Or give each user their own entry.
def per_user_key(request: Request) -> str:
    # `request.state.user_id` is populated by your authentication layer after
    # it has verified the caller — never read the identity straight off an
    # unverified request header (see the note below).
    user_id = getattr(request.state, "user_id", "anonymous")
    return (
        f"{request.method}{CACHE_KEY_SEPARATOR}"
        f"{request.headers.get('host', 'unknown')}{CACHE_KEY_SEPARATOR}"
        f"{request.url.path}{CACHE_KEY_SEPARATOR}"
        f"{request.query_params}{CACHE_KEY_SEPARATOR}{user_id}"
    )


@app.get("/me/dashboard")
@cache(ttl=60, private=True, key_builder=per_user_key)
async def my_dashboard(user: CurrentUser):
    return build_dashboard(user)
```

> [!CAUTION]
> The key builder decides who sees whose data, so the identity it reads must
> come from something already verified — a claim from a checked token, a user
> your dependency resolved, or a value your auth middleware wrote to
> `request.state`.
>
> ```python
> # ❌ Never do this: anyone can send this header.
> user_id = request.headers.get("x-user-id", "anonymous")
> ```
>
> A key built from a raw request header is a horizontal privilege escalation:
> sending `X-User-Id: <someone-else>` returns that user's cached response.

### Cache Hit Behavior

When a cached entry is valid (within TTL):
- **Default behavior**: Returns the cached content directly, with the status code and headers the handler originally produced, without re-executing the endpoint handler
- **With `If-None-Match` header**: Returns HTTP 304 Not Modified if the ETag matches
- **With `no-cache` directive**: Forces revalidation with fresh content before deciding on 304
- **With `private=True`**: Nothing is read from or written to the shared backend; the handler runs every time and only `If-None-Match` revalidation applies

This means **cached hits are extremely fast** - the endpoint handler function is never executed.

Only successful responses are stored. A response the handler *returns* with a
non-2xx status (for example `Response(..., status_code=404)`) is passed straight
through and never cached, so a transient error cannot replace or poison the last
good entry. `206 Partial Content` is excluded as well, since its body is only
meaningful for the `Range` request that produced it. `Set-Cookie` is never
stored or replayed.

### Atomic backend primitives

Every backend exposes atomic operations on top of `get`/`set`/`delete`, for
values that are read and written by many concurrent requests:

```python
import secrets

from fastapi_cachex import BackendProxy
from fastapi_cachex.types import CacheEntry

backend = BackendProxy.get()

# Fixed-window counter: created on first use, `ttl` applies only then.
hits = await backend.increment(f"resend:{user_id}", ttl=86400)
if hits > 3:
    raise TooManyRequests()

# One-shot value: of several concurrent callers exactly one gets the entry.
grant = await backend.get_and_delete(f"grant:{token}")

# Lock / slot: claim only if free, release only while it is still yours.
owner = CacheEntry(fingerprint="lock", content=secrets.token_bytes(16))
if await backend.set_if_absent(f"stream:{user_id}", owner, ttl=300):
    try:
        ...
    finally:
        await backend.delete_if_equals(f"stream:{user_id}", owner)
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
- `set_if_absent(key, value, ttl=None) -> bool` — stores `value` only when
  `key` does not exist (an expired key counts as absent) and reports whether it
  did. Memory checks under its lock, Redis uses `SET NX EX` and Memcached `ADD`.
- `delete_if_equals(key, expected) -> bool` — removes `key` only while it still
  holds `expected`, so a holder whose entry expired cannot release a lock that
  someone else has claimed since. Put a unique token in the entry you store and
  release with that same entry. Memory compares under its lock, Redis deletes
  through a Lua script that re-checks the value it compared, and Memcached uses
  `GETS` + a `CAS` write that expires the entry immediately (the classic
  protocol's `DELETE` takes no CAS token).

All four have a non-atomic fallback on `BaseCacheBackend`, so a third-party backend
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

**Configuring from a model**: `RedisConfig` is a pydantic model with the same
settings and validation, which is handy when they come from environment
variables or a settings file:

```python
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.backends.config import RedisConfig

config = RedisConfig(
    host="127.0.0.1",
    port=6379,
    password=None,  # SecretStr | None
    db=0,
    encoding="utf-8",  # how the client decodes server responses
    socket_timeout=1.0,  # seconds; applies to reads/writes
    socket_connect_timeout=1.0,
    key_prefix="fastapi_cachex:",
    protocol=2,  # RESP version, 2 or 3
)
backend = AsyncRedisCacheBackend.load_from_config(config)
BackendProxy.set(backend)
```

Keep `protocol=2` unless you need RESP3 features *and* your `hiredis` build
supports it (RESP3 needs hiredis >= 3.0). Redis 8.0 speaks RESP3, but an older
hiredis will fail to negotiate it.

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

- [Cache Flow Explanation](https://github.com/allen0099/FastAPI-CacheX/blob/master/docs/CACHE_FLOW.md)
- [Development Guide](https://github.com/allen0099/FastAPI-CacheX/blob/master/docs/DEVELOPMENT.md)
- [Known Limitations and Planned Work](https://github.com/allen0099/FastAPI-CacheX/issues)
- [Changelog](https://github.com/allen0099/FastAPI-CacheX/blob/master/CHANGELOG.md)
- [Contributing Guidelines](https://github.com/allen0099/FastAPI-CacheX/blob/master/docs/CONTRIBUTING.md)
- [Session Management Guide](https://github.com/allen0099/FastAPI-CacheX/blob/master/docs/SESSION.md) - Complete guide for session features
- [State Management Guide](https://github.com/allen0099/FastAPI-CacheX/blob/master/docs/STATE.md) - One-shot OAuth/CSRF state tokens
- [JWT Claims Guide](https://github.com/allen0099/FastAPI-CacheX/blob/master/docs/JWT_CLAIMS.md) - Claim design and extension points for JWT session tokens

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](https://github.com/allen0099/FastAPI-CacheX/blob/master/LICENSE) file for details.
