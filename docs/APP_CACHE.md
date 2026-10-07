# Application Cache

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


# Or instantiate directly, e.g. outside of a request, once a backend is set with
# BackendProxy.set(...). `lock` defaults to True
# (see "Stampede protection").
manager = CacheManager(key_prefix="myapp:", default_ttl=60)
await manager.set("user:42", {"name": "Alice"})
# Every ttl takes an int of seconds or a timedelta of whole seconds.
await manager.set("user:43", {"name": "Bob"}, ttl=timedelta(minutes=5))
user = await manager.get("user:42")  # {"name": "Alice"}
await manager.delete("user:42")
await manager.clear_prefix()  # clear everything under "myapp:"

# Compute-on-miss: `factory` runs only when the key is missing, expired, or
# undecodable. It may be sync or async.
profile = await manager.get_or_set("user:42", lambda: load_user(42), ttl=300)

# Store only if the key is free: of concurrent callers exactly one gets True.
if await manager.add(f"webhook:{event_id}", True, ttl=86400):
    await deliver_webhook(event_id)

# Glob over this manager's namespace. Only the pattern is a glob; the prefix
# is literal. With a prefix free of *?[]\ this uses the backend's native
# pattern support (Redis SCAN) rather than enumerating every key.
await manager.clear_pattern("user:*")  # matches "myapp:user:*"
```

Complete runnable example: [`examples/app_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/app_cache.py).

## Caching a function

`@cached` does what `get_or_set()` does for a plain function, keyed on its
arguments, so a loader or a call to another service is written once and
cached wherever it is called. From [`examples/app_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/app_cache.py):

<!-- fmt:off -->
```python
--8<-- "examples/app_cache.py:cached"
```
<!-- fmt:on -->

- The decorated function is always `await`-ed, even if it was `def` (a sync
  function then runs on the event loop, as a `get_or_set()` factory does).
  Its result goes through the manager's [JSON round-trip](#json-round-trip),
  so it must be JSON-serializable and comes back as JSON gives it, on the
  first call too.
- `ttl` takes seconds or a `timedelta`; without it the manager's
  `default_ttl` applies. `manager=` names the `CacheManager` to store
  through; without it the application's is used (what `AppCache` returns),
  resolved on each call, so one registered with `CacheManagerProxy.set()` at
  startup is picked up. `lock=False` skips the manager's
  [stampede protection](#stampede-protection) for that function.
- Without `key=` the key is `module.qualname:` followed by a SHA-256 of the
  arguments, bound to the signature with defaults applied, so `load(1)`,
  `load(user_id=1)` and `load(1, locale="en")` share one entry. The
  arguments are hashed as JSON, so they must be JSON-serializable; a call
  with one that is not raises `CacheXError`. `key="user:{user_id}"` is a
  `str.format` template over the arguments by name (a plain string is a fixed
  key), and `key=lambda self, user_id: f"user:{user_id}"` is called with
  them: use either for a method, whose `self` cannot be hashed, or for a
  model. The manager's `key_prefix` goes in front, as for every key it stores.
- `fn.cache_key(*args, **kwargs)` is the key a call uses (without the
  prefix) and `await fn.invalidate(*args, **kwargs)` drops its value,
  returning whether one was cached. On a method, `obj.load.invalidate(1)`
  binds `obj` as on a call.

## Behavior

- `get()` returns `None` (or a supplied `default=`) on a cache miss — it never
  raises for missing or corrupted entries.
- `set()` lets `TypeError` propagate for values that are not JSON-serializable.
- `get_or_set()` uses stampede protection by default (`lock=True`, per call
  or manager-wide with `CacheManager(lock=...)`), preventing concurrent misses
  from running `factory` simultaneously, with graceful fallback to computing
  directly if waiting times out. The distributed lock key is
  `lock:<prefix><key>` (`lock:cache:user:42` by default).
- `get_or_set()` returns the JSON-decoded value on a miss as well as on a hit
  (see [JSON round-trip](#json-round-trip)), so both paths give the same result.
- `add()` stores a value only when the key is free and returns whether it did.
  The check and the write are one atomic backend operation (`set_if_absent`),
  so it suits "do this once per key" work such as webhook or email
  deduplication. An expired key counts as free; a key holding an undecodable
  value does not, even though `get()` treats it as a miss.
- Keys live under their own `cache:`-prefixed namespace by default, separate from
  the HTTP route cache and OAuth state, so `clear()`/`clear_prefix()` never touch
  unrelated cache entries.
- The prefix is matched as a plain string prefix. A manager with
  `key_prefix="cache:"` therefore also clears the entries of one with
  `key_prefix="cache:users:"`, and an empty `key_prefix` makes `clear()` remove
  everything in the backend, including HTTP responses, locks, OAuth states and
  sessions. Give each manager a prefix that does not start with another's.
- `clear_pattern(pattern)` treats only `pattern` as a glob; `key_prefix` is
  always matched literally. With a prefix free of glob metacharacters
  (`*`, `?`, `[`, `]`, `\`) it hands `key_prefix + pattern` to the backend's
  `clear_pattern()` (Redis `SCAN MATCH`), and `pattern` uses the backend's
  glob syntax. A prefix that contains one, such as `cache[1]:`, cannot be
  passed on as a glob, so `clear_pattern()` lists every key with
  `get_all_keys()`, keeps those that start with the prefix and whose remainder
  matches `pattern` under `fnmatch.fnmatchcase`, and deletes them with
  `delete_many()`. That is slower on Redis, and `pattern` is then fnmatch
  syntax rather than Redis glob: case-sensitive, no backslash escapes, and
  `[!a]` rather than `[^a]` for negation. Constructing a `CacheManager` with
  such a prefix emits a `UserWarning`; pick a prefix without `*?[]\` to keep
  the fast path.
- The `AppCache` dependency creates and registers a default `CacheManager` the
  first time it is used; `CacheManagerProxy.set()` registers your own instead.

> [!NOTE]
> `CacheManager.clear()`/`clear_prefix()` are implemented via the backend's `get_all_keys()`
> and `delete_many()` (`DEL` in batches of 100 keys on Redis). Since Memcached doesn't
> support key enumeration (see [Backends](BACKENDS.md#memcached)), these
> methods — and `CacheManager.clear_pattern()` — are no-ops on a Memcached backend that
> return 0 with a `RuntimeWarning`;
> `get()`/`set()`/`add()`/`delete()`/`has()` work normally. Use Redis or the in-memory
> backend if you need bulk clearing. Do not fall back to the backend's own `clear()`
> on Memcached: `MemcachedBackend.clear()` issues `flush_all` and wipes the whole
> server, HTTP responses, sessions, locks and other applications' keys included.

## Stampede protection

When the factory is expensive (a slow database query, a rate-limited upstream
API) and the key is hot, cache expiry turns into simultaneous recomputations.
Distributed stampede protection built on `CacheLock` is on by default; you can
tune or turn it off per call or manager-wide (for `@cache` routes, see
[Concurrent misses](HTTP_CACHING.md#concurrent-misses)):

```python
# Per-call protection:
profile = await manager.get_or_set(
    "user:42",
    lambda: load_user(42),
    ttl=300,
    lock=True,  # default: the manager's lock, True unless set otherwise
    lock_ttl=30,  # lease upper bound for factory run (default: 60)
    wait_timeout=10,  # caller latency budget in seconds (default: None, wait while held)
    raise_on_timeout=False,  # True raises LockTimeoutError, False falls back to factory (default: False)
)

# Or manager-wide default:
manager = CacheManager(lock=False)  # compute on every concurrent miss
```

1. **Miss**: On a miss, callers attempt non-blocking lock acquisition using `CacheLock` under the key `lock:<prefix><key>` (`lock:cache:user:42` by default).
2. **Winner**: The winner re-checks the cache, invokes `factory`, stores the value in the backend, and releases the lock.
3. **Waiters**: Other callers poll the cache with exponential backoff (50ms base, 1.5x factor, 500ms cap) and randomized jitter (±10%) until the value appears. When `wait_timeout` is omitted, callers wait bounded by the holder's `lock_ttl` without an arbitrary deadline.
4. **Takeover**: If the winner fails or its lock expires, a waiting caller takes over the lock, re-checks the cache, and computes if necessary.
5. **Re-entrancy**: Recursive calls to `get_or_set()` for the same key in the same task automatically skip locking to avoid self-deadlock.
6. **Timeouts**: When an explicit `wait_timeout` elapses, `raise_on_timeout=False` logs a warning and falls back to direct factory computation (graceful degradation), while `raise_on_timeout=True` raises `LockTimeoutError`.

Ensure `lock_ttl` exceeds the expected execution time of `factory`. If `factory` outlives `lock_ttl`, the lock expires mid-run and a waiting caller may start a second computation.

### Cost {#cost}

Stampede protection is on by default since 0.4.0 ([#280](https://github.com/allen0099/FastAPI-CacheX/issues/280)), including the manager `AppCache` creates. It costs backend round trips on a miss; a hit is one `GET` either way. Counted on Redis and Memcached, where each step is a network round trip:

| Path | `lock=False` | `lock=True` |
|------|--------------|-------------|
| Hit | 1: `GET` | 1: `GET` |
| Miss, no other caller | 2: `GET`, `SET` | 6: `GET`, lock `SET NX` (Memcached `add`), re-check `GET`, `SET`, and the lock release, which reads the lock and deletes it only if it still holds this caller's token (2) |
| Each waiting caller | none: it runs `factory` itself | 2 at the start (`GET`, lock attempt), then 2 per poll (`GET`, lock attempt) until the value appears |

Waiters poll with the backoff above: about five polls in the first second, then one every 500 ms, so each waiter adds about four backend operations per second for as long as `factory` runs. The memory backend does the same steps in process. Pass `lock=False` to a call, or to `CacheManager(...)`, for keys where a duplicate computation is cheaper than the extra round trips; for `AppCache`, register your own manager with `CacheManagerProxy.set(CacheManager(lock=False))`.

## JSON round-trip

Values are stored as JSON (`json.dumps` with its defaults) and read back with
`json.loads`, so what you get back is the JSON-decoded form, not the object you
stored:

| Stored | Read back |
|---|---|
| `dict`, `list`, `str`, `int`, `float`, `bool`, `None` | unchanged |
| `tuple` | `list`: `(1, 2)` → `[1, 2]` |
| `dict` with `int`/`float`/`bool`/`None` keys | string keys: `{1: "a"}` → `{"1": "a"}` |
| `datetime`, `Decimal`, `UUID`, `set`, a pydantic model, … | `TypeError`, nothing stored |

`get_or_set()` applies this on a miss too: it encodes the factory's value once,
stores those bytes and returns them decoded, so the first call returns exactly
what later hits return. A value JSON cannot encode raises `TypeError` only
after `factory` has run, since the value doesn't exist before; convert it
first, e.g. `lambda: model.model_dump(mode="json")` or
`lambda: when.isoformat()`.

The full method list is in the [API reference](api/cache-manager.md).
