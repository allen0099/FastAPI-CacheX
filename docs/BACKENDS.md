# Backends

Every cache — HTTP responses, `CacheManager` values, sessions and OAuth states —
lives in one backend, registered once at startup with `BackendProxy.set()`.

## Choosing a backend

| Scenario | Recommended backend | Why |
|----------|---------------------|-----|
| Development and testing | MemoryBackend | Fast, no dependencies |
| Distributed systems | Redis | Async, efficient, supports pattern clearing |
| Simple caching | Memcached | Stable, mature (but no key enumeration, so no pattern/path clearing or monitoring) |
| Multi-process deployments | Redis | Shared cache, consistency |

All backends namespace their keys with a prefix (`fastapi_cachex:` by default,
`key_prefix=` to change it) to avoid conflicts with other applications.

## In-memory (default)

If you don't specify a backend, FastAPI-CacheX will use the in-memory cache by default.
This is suitable for development and testing purposes. The backend automatically runs
a cleanup task to remove expired entries every 60 seconds (`MemoryBackend(cleanup_interval=60)`).

```python
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex import BackendProxy

backend = MemoryBackend()
BackendProxy.set(backend)
```

> [!NOTE]
> The in-memory cache is not suitable for production with multiple processes.
> Each process maintains its own separate cache.

## Redis

Install the extra with `uv add "fastapi-cachex[redis]"`.

```python
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex import BackendProxy

backend = AsyncRedisCacheBackend(host="127.0.0.1", port=6379, db=0)
BackendProxy.set(backend)
```

- Fully async implementation
- Supports pattern-based key clearing
- Uses SCAN instead of KEYS for safe production use (non-blocking)
- Namespaced with `fastapi_cachex:` prefix by default; pass `key_prefix="myapp:cache:"`
  for multi-tenant scenarios

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

## Memcached

Install the extra with `uv add "fastapi-cachex[memcache]"` (note: `memcache`, not
`memcached`).

```python
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex import BackendProxy

backend = MemcachedBackend(servers=["localhost:11211"])
BackendProxy.set(backend)
```

**Limitations**:

- Pattern-based key clearing (`clear_pattern`) is not supported by the Memcached protocol
- Keys cannot be enumerated: `get_all_keys()`/`get_cache_data()` return empty
  results (with a `RuntimeWarning`), so the monitoring routes show nothing
- `clear_path()` deletes only the exact key given; `include_params` has no effect
- `clear()` issues `flush_all`, which wipes the whole Memcached server, not just this namespace
- A key Memcached would reject (over 250 bytes, whitespace, non-ASCII) is stored
  under its SHA-256 digest
- Consider using the Redis backend if you need pattern-based cache clearing

The synchronous pymemcache client runs in worker threads and is connection-pooled,
so concurrent requests never share a socket. Writes wait for the server's
acknowledgement (`default_noreply=False`), which keeps a value readable from
any pooled connection as soon as `set()` returns.

## Atomic backend primitives

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
  `DELETE` won. `StateManager.consume_state`, `StateManager.delete_state`,
  `CacheManager.delete` and `invalidate()` are built on it.
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

## TTL values

Every `ttl` argument (`set`, `set_if_absent`, `increment`, and the `CacheManager`
and `StateManager` methods and defaults built on them) is either `None`, meaning
the entry never expires, or a positive number of seconds. Zero and negative
values raise `ValueError`. The underlying stores disagree on what they mean:
Memcached reads an exptime of `0` as "never expire", Redis rejects `EX 0`, and
an in-process dict would expire the entry at once. A third-party backend should
call `fastapi_cachex.backends.base.validate_ttl(ttl)` in its `set` to follow
the same rule. (`@cache(ttl=0)` is separate: it sends `max-age=0` and never
passes `0` to the backend; see [HTTP caching](HTTP_CACHING.md).)

How each backend stores entries is described in
[Cache flow](CACHE_FLOW.md#backend-storage-formats); the classes themselves are in
the [API reference](api/backends.md).
