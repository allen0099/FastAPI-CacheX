# Backends

Every cache — HTTP responses, `CacheManager` values and `CacheLock` locks —
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
the interval must be positive). It stores and returns copies of each
`CacheEntry`, so changing an entry you read or wrote does not change the
cache, as on Redis and Memcached, which decode a new entry on every read
(before 0.4.1, the memory backend handed out the stored object itself).

When `@cache`, `CacheBackend` or `AppCache` registers this fallback because no
backend was set, the `fastapi_cachex.proxy` logger logs a `WARNING` once per
process, since its cache is per process: under multiple workers, invalidation
reaches only one worker. Set the backend explicitly at startup to silence it;
an explicit `MemoryBackend` does not log it:

```python
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex import BackendProxy

backend = MemoryBackend()
BackendProxy.set(backend)
```

> [!NOTE]
> The in-memory cache is not suitable for production with multiple processes.
> Each process maintains its own separate cache.

Creating a `MemoryBackend`, explicitly or as the fallback, emits a
`UserWarning` when the `WEB_CONCURRENCY` environment variable is an integer
greater than 1. uvicorn (`--workers`) and gunicorn (`-w`) read that variable as
their default worker count, and platforms such as Heroku set it. With several
workers, `invalidate()` and the `clear*` methods reach one worker only,
`CacheLock` and `get_or_set(lock=True)` exclude only within one worker, and
counters are per worker. The check is a hint, not a guarantee: a worker count
given only on the command line, or several single-worker replicas, are not
detected. If the variable is set but the process really is the only worker,
silence the warning:

```python
import warnings

warnings.filterwarnings("ignore", message="memory backend with several workers")
```

The cleanup task starts on the event loop of the first cache call. If a later call
runs on a different loop, for example after the first loop was closed, the task is
started again there. To stop it on shutdown, `await backend.aclose()` cancels the
task and waits until it has finished (see [Closing a backend](#closing-a-backend)).
`stop_cleanup()` only requests cancellation.

By default the cache has no size limit: entries leave only when they expire, so
memory grows with request rate × `ttl` × body size, per worker, and a client can
grow it on purpose by varying the query string of a cached route.
`MemoryBackend(max_entries=10_000)` caps the number of entries: storing one more
evicts the least recently used entry, expired or not (a read hit and every
write count as a use). Size the limit from the entries you expect times the
workers you run, since each worker has its own cache; the limit counts entries,
not bytes.

```python
backend = MemoryBackend(max_entries=10_000)
```

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
  always adds the prefix itself, so leave the prefix out of the pattern. A pattern that
  starts with the prefix is not stripped: it matches only logical keys that start with
  the prefix (see [Migrating to 0.4.0](MIGRATING_0_4.md#redis-clear-pattern))

**Configuring from a model**: `RedisConfig` is a pydantic model with the same
settings, validated (port range, `db >= 0`, `protocol` 2 or 3), which is handy when they come from environment
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

The client reads raw bytes, and entries are written as UTF-8 JSON. There is no
`encoding` option: 0.4.0 removed it from both `RedisConfig` and `AsyncRedisCacheBackend`,
and passing `encoding` or `decode_responses` to the backend raises `TypeError`. See
[Migrating to 0.4.0](MIGRATING_0_4.md#redis-encoding).

Keep `protocol=2` unless you need RESP3 features *and* your `hiredis` build
supports it (RESP3 needs hiredis >= 3.0). Redis 8.0 speaks RESP3, but an older
hiredis will fail to negotiate it.

Complete runnable example: [`examples/redis_backend.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/redis_backend.py).

### URLs, TLS, ACL users and Sentinel {#redis-connecting}

A managed Redis usually wants TLS, an ACL user, or both. Give the backend a
URL, as redis-py's `from_url` takes it (`redis://`, `rediss://` for TLS,
`unix://` for a socket), or the same things as arguments:

```python
from fastapi_cachex.backends import AsyncRedisCacheBackend

# TLS and an ACL user, from a URL ...
backend = AsyncRedisCacheBackend.from_url(
    "rediss://cache-user:secret@redis.example.com:6380/0",
    key_prefix="myapp:",
)

# ... or as arguments
backend = AsyncRedisCacheBackend(
    host="redis.example.com",
    port=6380,
    username="cache-user",
    password="secret",
    ssl=True,
)
```

`from_url(url, **kwargs)` is `AsyncRedisCacheBackend(url=url, **kwargs)`, and
`key_prefix`, the timeouts, `protocol` and redis-py options are passed as
before. With a URL:

- `host` and `port` are not used. `password`, `db` and `username` apply only
  where the URL does not name its own, so a URL without credentials can take
  the password from a secret store.
- TLS is the `rediss://` scheme. Passing `ssl=`, or a `connection_pool=`,
  together with a URL raises `CacheXError`.
- An option in the URL's query string (`?socket_timeout=5`) wins over the
  argument of the same name. `decode_responses` and `encoding` are refused
  there as they are as arguments.
- The retry policy is the one a backend built from `host` and `port` gets
  (redis-py's default, see [Failing fast](#failing-fast-when-redis-is-down)),
  unless you pass `retry=`. In 0.5.4 a backend built from a URL did not retry.

`username`, `ssl` and the other `ssl_*` options (`ssl_ca_certs`,
`ssl_cert_reqs`, ...) are redis-py's own and go straight to its client.
`RedisConfig` has `username`, `ssl` and `url` fields too, with the same rules;
`url` is a `SecretStr`, since it may hold the password.

For **Sentinel**, build redis-py's pool and pass it as `connection_pool=`. The
backend's `host`, `port`, `password`, `db`, timeouts and `protocol` are then
not used, so set what you need on the pool, and close the pool yourself (see
[Closing a backend](#closing-a-backend)):

```python
from redis.asyncio.sentinel import Sentinel, SentinelConnectionPool

from fastapi_cachex.backends import AsyncRedisCacheBackend

sentinel = Sentinel([("sentinel-1", 26379), ("sentinel-2", 26379)], socket_timeout=1.0)
pool = SentinelConnectionPool(
    "mymaster", sentinel, socket_timeout=1.0, socket_connect_timeout=1.0
)
backend = AsyncRedisCacheBackend(connection_pool=pool)
```

**Redis Cluster is not supported.** The backend uses a single-node client:
`SCAN` would see one node, and the multi-key commands behind `delete_many()`,
`get_many()`, `set_many()` and the `clear*` methods fail across hash slots.

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
  `CacheManager` and `CacheLock`, which raise
  [`BackendUnavailableError`](#backend-unavailable) instead of
  failing open, see those errors sooner.

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

backend = MemcachedBackend(
    servers=["localhost:11211"],
    connect_timeout=0.5,  # seconds to open a connection
    timeout=1.0,  # seconds to wait for a reply
)
BackendProxy.set(backend)
```

`connect_timeout` and `timeout` (seconds; 1 by default,
[5 before 0.6.0](MIGRATING_0_6.md#memcached-timeouts)) bound each connection
attempt and each wait for a reply; a call that sends several commands
(`delete_many`, the compare-and-write operations) can take several times
`timeout`. Every Memcached call runs in a worker thread, which it holds until
the server answers or the timeout passes, so keep them low if an unreachable
server must not tie up the thread pool.

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
  Memcached server, not just this namespace, and emits a `RuntimeWarning` each time.
  `CacheManager.clear()` is different: it
  enumerates keys, so on Memcached it deletes nothing (see
  [Application cache](APP_CACHE.md))
- A key Memcached would reject (over 250 bytes, whitespace, non-ASCII) is stored
  under its SHA-256 digest, behind the `key_prefix`. The prefix itself is
  therefore checked when the backend is built: one with whitespace, control
  characters or non-ASCII, or of 250 bytes or more, raises `ValueError`, and
  one over 186 bytes, which leaves no room for the 64-character digest, warns
  (before 0.4.1 such a prefix made calls fail with
  `MemcacheIllegalInputError`)
- A `ttl` whose expiry falls after 2038-01-19 raises `ValueError` (see
  [TTL values](#ttl-values))
- Values larger than the server's item size limit (1 MB by default, `memcached -I`)
  are rejected ([`compress_min_size`](#compressing-large-entries) makes most of
  them fit). `@cache` logs it and serves the response unstored
  (see [When the backend fails](HTTP_CACHING.md#when-the-backend-fails)).
  `CacheManager` (`set`, `add`, `get_or_set`), `@cached` and direct `set()`,
  `set_if_absent()` and `set_if_equals()` calls raise `ValueTooLargeError`, a
  `CacheXError` whose message gives the encoded size and a digest of the key. Nothing is stored, and the server also drops
  the value a refused `set()` would have replaced, so the key reads as missing
  afterwards; a refused `set_if_absent()` leaves the existing value. A
  `set_many()` with one value over the limit raises it too, and may have
  stored the rest of the batch (see
  [Batch reads and writes](#batch-reads-and-writes)). The error
  is chained to pymemcache's `MemcacheServerError` and is
  also an instance of it, which is all these callers raised before 0.5.4
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

When a server cannot be reached, every call that would go to it raises
[`BackendUnavailableError`](#backend-unavailable), and the server is tried again
after one second. Before 0.3.8, calls in the second after a
failure returned made-up results instead: `get()` a miss, `set()` nothing (the write
was lost), `increment()` a fresh counter of 0. With several servers, a failed one is
taken out of rotation at once and its keys go to the remaining servers until it
answers again.

## Closing a backend

Every backend has `aclose()`, which releases what it holds open. Call it on
shutdown, at the end of the FastAPI lifespan, as [`examples/redis_backend.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/redis_backend.py) does:

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

## Compressing large entries {#compressing-large-entries}

A JSON response of a few hundred kB is stored as it is: memory on the server,
network time on every hit, and on Memcached nothing at all once the entry is
over the item size limit. `compress_min_size` on the Redis and Memcached
backends stores an entry of at least that many bytes compressed with zlib,
from the standard library:

```python
backend = AsyncRedisCacheBackend(compress_min_size=4096)
backend = MemcachedBackend(servers=["127.0.0.1:11211"], compress_min_size=4096)
```

`RedisConfig` has the same field. It is off by default.

| Stored entry (JSON list of records) | Compressed | Extra time per write | Extra time per hit |
|---|---|---|---|
| 14 kB | 3.4 kB (24%) | 0.09 ms | 0.04 ms |
| 140 kB | 32 kB (23%) | 1.0 ms | 0.5 ms |
| 1.4 MB | 317 kB (22%) | 9.7 ms | 5.7 ms |

Measured on one machine with CPython 3.15; take the ratios, not the
milliseconds. The size counted against the threshold is that of the stored
entry (body, headers and metadata as JSON), which is somewhat larger than the
response body.

- **The time is spent on the event loop.** Compressing and decompressing are
  plain CPU work in the request's own task, so a multi-megabyte entry holds up
  the other requests of the worker for those milliseconds. A threshold of a
  few kB keeps small entries, where there is little to gain, uncompressed.
- **Every reader understands both forms**, whatever its own setting, so the
  threshold can be changed or switched off at any time, and backends with
  different settings can share a server. The compare-and-write primitives
  compare entries, not stored bytes, so locks and `add()` are unaffected.
- **Enable it only once every process runs 0.6.3 or later.** An older release
  reads a compressed entry as a miss.
- Counters are never compressed, and the memory backend has no such option:
  it keeps entries as objects.
- On Memcached the item size limit applies to the compressed entry, so a
  response of several MB of JSON fits under the default 1 MB.

## When the server is unavailable {#backend-unavailable}

When the Redis or Memcached server cannot be reached, the backend raises
`BackendUnavailableError`, a `CacheXError`. That covers a refused or reset
connection, a timeout, and a Memcached server out of rotation ("All servers
seem to be down"). The memory backend never raises it. So code that wants to
handle "the cache is down" needs one `except`, whichever backend is set:

```python
from fastapi_cachex import AppCache, BackendUnavailableError


@app.get("/report")
async def report(cache: AppCache) -> dict[str, int]:
    try:
        return await cache.get_or_set("report", build_report, ttl=300)
    except BackendUnavailableError:
        return build_report()  # serve it uncached while the cache is down
```

The error is chained to the client's own exception (`__cause__`), and is also
an instance of that exception's type: a refused Memcached connection is a
`BackendUnavailableError` and a `ConnectionRefusedError`, a Redis one a
`BackendUnavailableError` and a `redis.exceptions.ConnectionError`. An
`except` clause naming the client's exception, written before 0.5.1, keeps
catching it. Errors that are not about reaching the server are not
turned into it: an invalid argument, a key that holds something else, a value
Memcached refuses as too large (a `ValueTooLargeError`, see
[Memcached](#memcached)).

`@cache` is not affected: it [fails open](HTTP_CACHING.md#when-the-backend-fails)
on any backend error, this one included. `CacheManager`, `CacheLock` and direct
backend calls let it propagate.

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
  raises `CacheXError`. The memory backend and the base fallback use the signed
  64-bit range too. An increment whose result would leave the range raises
  `CacheXError` and leaves the counter unchanged on every backend (before
  0.4.1, Redis raised its own `ResponseError`, Memcached wrapped around to a
  small number and memory grew without limit). On Memcached the wrap is
  detected after the `INCR` and then undone with a second `INCR`, so an
  increment or decrement on the same key that lands between the two can see
  the wrapped value. `delta`
  must be an `int` within the signed 64-bit range; anything else raises
  `TypeError` or `ValueError` before the backend is touched.
- `get_and_delete(key) -> CacheEntry | None` — Memory pops under its lock, Redis
  uses `GETDEL` (server 6.2+) and Memcached uses `GETS` + a `CAS` write with
  `exptime=-1` (retrying if another writer replaced the value in between). If
  writers keep replacing it for 16 attempts in a row, Memcached raises
  `CacheXError` rather than report the key as missing.
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
  `ttl` is required: `None` raises `TTLTypeError`.
  Memory updates under its lock, Redis compares in Python then executes a Lua
  script (`GET` compare + `EXPIRE`), and Memcached uses `GETS` + `CAS` writing the
  same bytes with the new exptime (`TOUCH` takes no CAS token).
- `set_if_equals(key, expected, value, ttl=None) -> bool` — stores `value` only
  while `key` still holds `expected`: a compare-and-set that fails if anything
  changed, deleted or expired the key since the caller read it. Memory
  compares under its lock, Redis compares in Python then runs a Lua script
  (`GET` compare + `SET`, with `EX` when `ttl` is set), and Memcached uses
  `GETS` + a `CAS` write of the new value.

All six have a non-atomic fallback on `BaseCacheBackend`, so a third-party backend
that only implements the abstract methods keeps working; override them to get
real atomicity. The fallbacks rely on `delete()` returning whether the key held
an entry; since 0.5.0 a `delete()` that still returns `None`, as in 0.3.x,
counts as not removed (see [Backend `delete()` returning `None`](MIGRATING_0_5.md#backend-delete-none)).

Complete runnable example: [`examples/rate_limit.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/rate_limit.py).

### Batch reads and writes {#batch-reads-and-writes}

`get_many` and `set_many` read and write several keys in one round trip per
server. `CacheManager.get_many()` and `set_many()` are built on them.

```python
await backend.set_many({"a": entry_a, "b": entry_b}, ttl=300)
found = await backend.get_many(["a", "b", "c"])  # {"a": entry_a, "b": entry_b}
```

- `get_many(keys) -> dict[str, CacheEntry]` — returns the entries found, by
  key. A key that is missing, expired or holds bytes that are not an entry is
  left out, and a key given twice is read once. Memory reads under its lock,
  Redis sends one `MGET` and Memcached one `get` with every key per server.
  An empty `keys` does no I/O.
- `set_many(entries, ttl=None) -> None` — stores every entry of a mapping,
  all with the same `ttl`, which is validated before anything is written. An
  empty mapping does no I/O. Whether the batch is atomic depends on the
  backend:
    - **Memory**: atomic. The whole batch is stored under the backend's lock.
      With `max_entries`, a batch larger than the limit keeps its last
      `max_entries` entries.
    - **Redis**: atomic. One `SET` per entry inside a single `MULTI`/`EXEC`
      (`MSET` takes no expiry), sent in one round trip. A concurrent reader
      sees the batch entirely or not at all, and a batch the server refuses,
      for example at its memory limit, stores nothing.
    - **Memcached**: not atomic. The entries go out as one batch of `set`
      commands per server, and the server stores each on its own. If one
      value is over the item size limit the call raises `ValueTooLargeError`,
      whose message gives the size and key digest of the largest value in the
      batch, and the other entries may have been stored. The same holds when a
      server goes away mid-batch, or when one of several servers fails.

Both have a fallback on `BaseCacheBackend` that calls `get` or `set` once per
key, so a third-party backend works without overriding them. The `set_many`
fallback stops at the first `set` that raises, leaving the entries before it
stored.

## TTL values

Every `ttl` argument (`set`, `set_many`, `set_if_absent`, `set_if_equals`, `increment`, and the `CacheManager`
and `CacheLock` methods and defaults built on them) is either `None`, meaning
the entry never expires, or a number of seconds from 1 up to `MAX_TTL`
(2**31 - 1, about 68 years), as an `int` or a `datetime.timedelta` of whole
seconds (`timedelta(minutes=5)` is stored as `300`). The checks run before any
backend I/O:

- Zero, negative and larger values raise `TTLValueError`. The underlying stores
  disagree on what `0` means: Memcached reads it as "never expire", Redis
  rejects `EX 0`, and an in-process dict would expire the entry at once.
- A `float`, a `bool` or any other type raises `TTLTypeError`. A float worked
  only on the memory backend, and `True` was taken as one second. A
  `timedelta` with a fraction of a second (`timedelta(seconds=1.5)`) raises
  `TTLValueError`, since every backend keeps time in whole seconds.
- Memcached cannot store an expiry after 2038-01-19 (its exptime is a signed
  32-bit timestamp), so the Memcached backend raises `TTLValueError` for a
  `ttl` that reaches past it instead of accepting a write it would drop at once.

Both classes derive from `InvalidTTLError`, a `CacheXError`, and are raised
for an invalid `ttl` wherever it is passed: the backends, `CacheManager`,
`CacheLock`, `@cached` and `@cache` (`ttl` and `stale_ttl`). Catch
`InvalidTTLError` to handle all of them. `TTLTypeError` is also a `TypeError`
and `TTLValueError` a `ValueError`, which is what the backends, `CacheManager`
and `CacheLock` raised before 0.5.1, so an existing `except TypeError` or
`except ValueError` keeps working, as does an `except CacheXError` around
`@cache`:

```python
from fastapi_cachex import InvalidTTLError


try:
    await manager.set("report", data, ttl=user_supplied_ttl)
except InvalidTTLError as e:
    raise HTTPException(422, detail=str(e)) from e
```

A third-party backend should call `fastapi_cachex.backends.base.validate_ttl(ttl)`
in its `set` and store the `int` it returns, to follow the same rules and
accept a `timedelta`, and `validate_delta(delta)` in `increment`. (`@cache(ttl=0)` is separate: it sends `max-age=0` and never
passes `0` to the backend; see [HTTP caching](HTTP_CACHING.md).)

How each backend stores entries is described in
[Cache flow](CACHE_FLOW.md#backend-storage-formats); the classes themselves are in
the [API reference](api/backends.md).
