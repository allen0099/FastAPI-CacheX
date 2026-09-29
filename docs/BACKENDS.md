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

The Redis and Memcached backends namespace their keys with a prefix
(`fastapi_cachex:` by default, `key_prefix=` to change it) to avoid conflicts with
other applications on the same server. `MemoryBackend` lives inside one process
and has no prefix.

## In-memory (default)

If you don't specify a backend, FastAPI-CacheX will use the in-memory cache by default.
This is suitable for development and testing purposes. The backend automatically runs
a cleanup task to remove expired entries every 60 seconds (`MemoryBackend(cleanup_interval=60)`;
the interval must be positive).

When `@cache`, `CacheBackend` or `AppCache` registers this fallback because no
backend was set, the `fastapi_cachex.proxy` logger logs a `WARNING` once per
process, since its cache is per process: under multiple workers, invalidation
reaches only one worker. Set the backend explicitly at startup to silence it;
an explicit `MemoryBackend` stays silent:

```python
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex import BackendProxy

backend = MemoryBackend()
BackendProxy.set(backend)
```

> [!NOTE]
> The in-memory cache is not suitable for production with multiple processes.
> Each process maintains its own separate cache.

The cleanup task starts on the event loop of the first cache call. If a later call
runs on a different loop, for example after the first loop was closed, the task is
started again there. To stop it on shutdown, `await backend.aclose()` cancels the
task and waits until it has finished (see [Closing a backend](#closing-a-backend)).
`stop_cleanup()` only requests cancellation.

`clear_pattern()` matches whole keys case-sensitively on every platform, like Redis.
The glob syntax is Python's `fnmatch`, which differs from Redis in two places: negate
a character class with `[!...]` (Redis uses `[^...]`), and escape a special character
by putting it in brackets, as in `[*]` (Redis also accepts `\*`). `*`, `?` and
`[abc]` behave the same on both.

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
- `clear()`, `clear_pattern()` and `clear_path()` delete only keys under this backend's
  `key_prefix`; other applications on the same server keep their keys
- Only the pattern you pass to `clear_pattern()` is a glob. The key prefix and the path
  given to `clear_path()` are matched literally, so `*`, `?`, `[` or `]` in them cannot
  reach keys outside the prefix or miss the path
- `clear_pattern()` matches the logical key, the key without the backend prefix, and
  always adds the prefix itself. Before 0.3.8 a pattern that started with the prefix
  was matched with the prefix stripped. That form still works when it is the only one
  that matches anything, with a `DeprecationWarning`, until 0.4.0

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
    socket_timeout=1.0,  # seconds; applies to reads/writes
    socket_connect_timeout=1.0,
    key_prefix="fastapi_cachex:",
    protocol=2,  # RESP version, 2 or 3
)
backend = AsyncRedisCacheBackend.load_from_config(config)
BackendProxy.set(backend)
```

Leave `encoding` out, of both `RedisConfig` and `AsyncRedisCacheBackend`: it is deprecated
and removed in 0.4.0, and setting it at all emits a `DeprecationWarning`. Entries are always
written as UTF-8 JSON, and the client decodes replies with `encoding`, so any value other than
UTF-8 also corrupts non-ASCII content on the way back (with `"latin-1"`, a stored `b"\xe9"`
reads back as `b"\xc3\xa9"`), and the backend emits a `RuntimeWarning` for it. See
[Migrating to 0.4.0](MIGRATING_0_4.md#redis-encoding).

Keep `protocol=2` unless you need RESP3 features *and* your `hiredis` build
supports it (RESP3 needs hiredis >= 3.0). Redis 8.0 speaks RESP3, but an older
hiredis will fail to negotiate it.

Complete runnable example: [`examples/redis_backend.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/redis_backend.py).

### Failing fast when Redis is down

`@cache` [fails open](HTTP_CACHING.md#when-the-backend-fails), but only once
the backend has given up. By default redis-py 8 retries a command that fails
with a connection error or timeout 10 times, with exponential backoff capped at
1 s between attempts. With Redis refusing connections, each `get` or `set` then
takes about 3 to 4 s to fail, so a cached request, which reads and then writes,
takes about 7 s. When the host does not answer at all, each of the 11 attempts
also waits for `socket_connect_timeout` (1 s by default), about 15 s per
command and 30 s per request.

Every keyword argument the backend does not name itself goes to the redis-py
client, so the retry policy and the timeouts can be set there:

```python
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from fastapi_cachex import BackendProxy
from fastapi_cachex.backends import AsyncRedisCacheBackend

backend = AsyncRedisCacheBackend(
    host="127.0.0.1",
    port=6379,
    retry=Retry(NoBackoff(), 0),  # no retries: the first error is final
    socket_connect_timeout=0.25,  # seconds to open a connection
    socket_timeout=0.5,  # seconds to wait for a reply
)
BackendProxy.set(backend)
```

With these settings a refused connection fails at once and an unreachable host
after about 0.25 s. The costs:

- Without retries, a single transient error, such as a connection reset in
  the middle of a command, fails that command. `Retry(NoBackoff(), 1)` retries
  once, immediately.
- `socket_timeout` also bounds slow replies. Set both timeouts above your
  normal Redis latency, including for the largest entries you cache.
- The settings apply to every command the backend sends, not only to `@cache`.
  `CacheManager`, `StateManager`, `CacheLock` and sessions, which raise backend
  errors instead of failing open, see those errors sooner.

`RedisConfig` has no `retry` field, so pass it to the constructor.

## Memcached

Install the extra with `uv add "fastapi-cachex[memcached]"`. Before 0.3.8 it was
called `memcache`; 0.4.0 removed that name. An unknown extra only produces a
warning at install time, so `fastapi-cachex[memcache]` installs without
`pymemcache` and `MemcachedBackend` raises when it is constructed. See
[Migrating to 0.4.0](MIGRATING_0_4.md#memcache-extra).

```python
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex import BackendProxy

backend = MemcachedBackend(servers=["localhost:11211"])
BackendProxy.set(backend)
```

**Limitations**:

- Pattern-based key clearing (`clear_pattern`) is not supported by the Memcached protocol:
  it returns 0 with a `RuntimeWarning`
- Keys cannot be enumerated: `get_all_keys()`/`get_cache_data()` return empty
  results (with a `RuntimeWarning`), so the monitoring routes show nothing
- `clear_path()` cannot find HTTP cache entries: it deletes only a key named exactly
  as the path, ignores `include_params` and emits a `RuntimeWarning` on every call.
  To drop a cached route's entry after a write, call
  [`invalidate(request)`](HTTP_CACHING.md#invalidating-a-single-cached-route), which
  rebuilds the exact key
- `backend.clear()` (`MemcachedBackend.clear()`) issues `flush_all`, which wipes the whole
  Memcached server, not just this namespace. `CacheManager.clear()` is different: it
  enumerates keys, so on Memcached it deletes nothing (see
  [Application cache](APP_CACHE.md))
- A key Memcached would reject (over 250 bytes, whitespace, non-ASCII) is stored
  under its SHA-256 digest
- A `ttl` whose expiry falls after 2038-01-19 raises `ValueError` (see
  [TTL values](#ttl-values))
- Values larger than the server's item size limit (1 MB by default, `memcached -I`)
  are rejected with an error. `@cache` logs it and serves the response unstored
  (see [When the backend fails](HTTP_CACHING.md#when-the-backend-fails)); other
  callers get the error
- Consider using the Redis backend if you need pattern-based cache clearing

The synchronous pymemcache client runs in worker threads and is connection-pooled,
so concurrent requests never share a socket. Writes wait for the server's
acknowledgement (`default_noreply=False`), which keeps a value readable from
any pooled connection as soon as `set()` returns. Each call takes a single trip
to a worker thread, including the multi-step
[atomic operations](#atomic-backend-primitives).
`delete_many()` sends one `DELETE` per key within that call and returns how many
of the keys existed; before 0.3.8 it took one thread trip per key and returned
how many keys it was given.

When a server cannot be reached, every call that would go to it raises, and the
server is tried again after one second. Before 0.3.8, calls in the second after a
failure returned made-up results instead: `get()` a miss, `set()` nothing (the write
was lost), `increment()` a fresh counter of 0. With several servers, a failed one is
taken out of rotation at once and its keys go to the remaining servers until it
answers again.

## Closing a backend

Every backend has `aclose()`, which releases what it holds open. Call it on
shutdown, at the end of the FastAPI lifespan:

<!-- fmt:off -->
```python
--8<-- "examples/redis_backend.py:lifespan"
```
<!-- fmt:on -->

The same lifespan works for every backend. A backend is also an async context
manager, so `async with MemcachedBackend(servers=[...]) as backend:` closes it
when the block ends, including when the block raises.

| Backend | `aclose()` |
|---------|------------|
| `MemoryBackend` | Cancels the cleanup task and waits until it has finished |
| `AsyncRedisCacheBackend` | Closes the redis-py client and the connection pool it created |
| `MemcachedBackend` | Closes every pooled socket to every server, in a worker thread |

- Calling `aclose()` more than once is safe.
- The backend owns the client it creates, so `aclose()` closes `backend.client`
  too; close the backend rather than reaching into the client. A Redis
  connection pool you create yourself and pass as `connection_pool=` is left
  open, as redis-py does: whoever creates a pool closes it.
- A closed backend is not locked: the Redis and Memcached clients reconnect on
  the next call, and `MemoryBackend` restarts its cleanup task, so a backend used
  after `aclose()` needs another `aclose()`.
- `BackendProxy.set(None)` only unregisters the backend; it does not close it.
- A custom backend inherits a no-op `aclose()` from `BaseCacheBackend`; override
  it when the backend holds connections or tasks.
- Without `aclose()`, open sockets are closed only by the garbage collector,
  which may emit `ResourceWarning`s at shutdown. Before 0.3.9 only
  `MemoryBackend` had `aclose()`, and the Redis and Memcached backends were
  closed through `backend.client`.

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
  Memcached uses `ADD` + `INCR`/`DECR` (Memcached counters stop at 0).
  Memcached keeps time in whole seconds, so a new counter with a short `ttl`
  can expire between its `ADD` and the `INCR`; Memcached then retries `ADD` +
  `INCR`, starting a new window at `delta`, and raises `CacheXError` only if
  the counter vanishes on all 16 attempts. The counter is visible through `get()` as a `CacheEntry` with fingerprint
  `COUNTER_FINGERPRINT` and the decimal value as content, so `delete`/`clear*`
  and the monitoring routes treat it like any other entry. Incrementing a key
  that holds anything else raises `CacheXError` on every backend, even a cached
  response whose body is a number. A counter written with
  `set(key, counter_entry(n))` can be incremented on every backend, as long as
  `n` fits the server's counters: Redis counters are signed 64-bit, so there `n`
  must be from -2**63 to 2**63 - 1, and Memcached counters are unsigned, so there
  `n` must be from 0 to 2**64 - 1. Incrementing a counter outside that range
  raises `CacheXError`. `delta`
  must be an `int` within the signed 64-bit range; anything else raises
  `TypeError` or `ValueError` before the backend is touched.
- `get_and_delete(key) -> CacheEntry | None` — Memory pops under its lock, Redis
  uses `GETDEL` (server 6.2+) and Memcached uses `GETS` + a `CAS` write with
  `exptime=-1` (retrying if another writer replaced the value in between). If
  writers keep replacing it for 16 attempts in a row, Memcached raises
  `CacheXError` rather than report the key as missing.
  `StateManager.consume_state`, `StateManager.delete_state`,
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
- `expire_if_equals(key, expected, ttl) -> bool` — updates the TTL on `key` to `ttl`
  seconds only while it still holds `expected`, so a long-running lock holder can
  renew its lease without risking overwriting someone else's lock if it expired.
  Memory updates under its lock, Redis compares in Python then executes a Lua
  script (`GET` compare + `EXPIRE`), and Memcached uses `GETS` + `CAS` writing the
  same bytes with the new exptime (`TOUCH` takes no CAS token).

All five have a non-atomic fallback on `BaseCacheBackend`, so a third-party backend
that only implements the abstract methods keeps working; override them to get
real atomicity.

Complete runnable example: [`examples/rate_limit.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/rate_limit.py).

## TTL values

Every `ttl` argument (`set`, `set_if_absent`, `increment`, and the `CacheManager`
and `StateManager` methods and defaults built on them) is either `None`, meaning
the entry never expires, or an `int` number of seconds from 1 up to `MAX_TTL`
(2**31 - 1, about 68 years). The checks run before any backend I/O:

- Zero, negative and larger values raise `ValueError`. The underlying stores
  disagree on what `0` means: Memcached reads it as "never expire", Redis
  rejects `EX 0`, and an in-process dict would expire the entry at once.
- A `float`, a `bool` or any other type raises `TypeError`. A float worked
  only on the memory backend, and `True` was taken as one second. Convert a
  `timedelta` with `int(td.total_seconds())`.
- Memcached cannot store an expiry after 2038-01-19 (its exptime is a signed
  32-bit timestamp), so the Memcached backend raises `ValueError` for a `ttl`
  that reaches past it instead of accepting a write it would drop at once.

A third-party backend should call `fastapi_cachex.backends.base.validate_ttl(ttl)`
in its `set` to follow the same rules, and `validate_delta(delta)` in
`increment`. (`@cache(ttl=0)` is separate: it sends `max-age=0` and never
passes `0` to the backend; see [HTTP caching](HTTP_CACHING.md).)

How each backend stores entries is described in
[Cache flow](CACHE_FLOW.md#backend-storage-formats); the classes themselves are in
the [API reference](api/backends.md).
