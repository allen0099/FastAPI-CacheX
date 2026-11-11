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

# Several keys in one backend call. Keys that are not cached are left out.
await manager.set_many({"user:1": {"name": "Ann"}, "user:2": {"name": "Ben"}}, ttl=300)
users = await manager.get_many(["user:1", "user:2", "user:3"])  # no "user:3"

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
  and is then validated into the function's return annotation (a
  [typed read](#typed-reads)), on the first call too; `type_=None` returns the
  JSON form.
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
  raises for missing or corrupted entries. A stored `None` (JSON `null`) reads
  back as `None` too, so `get()` alone cannot tell "cached as nothing" from
  "not cached", and code that caches a negative result this way recomputes it
  on every call. To tell them apart, pass a sentinel as the default, check
  `has()` first, or use `get_or_set()`, which runs `factory` only on a real
  miss and returns the stored `None` otherwise:

  ```python
  missing = object()
  value = await cache.get("user:42", default=missing)
  if value is missing:
      ...  # not cached; a cached None comes back as None
  ```
- `set()` stores what JSON can encode, and what pydantic can turn into JSON
  (models, dataclasses, `datetime`, `Decimal`, `UUID`, sets; see
  [JSON round-trip](#json-round-trip)). Any other value raises `TypeError`.
- `get_many(keys, type_=None)` and `set_many(mapping, ttl=None)` read and
  write several keys in one backend call, with the same prefix, encoding and
  `ttl` rules as `get()` and `set()`. `get_many()` returns a dict of the keys
  it found. A key that is missing, expired or undecodable (or, with `type_`,
  holds a value that does not fit) is left out, so a stored `None` is in the
  dict and a miss is not; a key given twice is read once. `set_many()`
  encodes every value before it writes anything, so a value that cannot be
  encoded raises `TypeError` and stores nothing. The write itself is all or
  nothing on the memory and Redis backends. On Memcached each key is stored
  on its own, so a failure can leave part of the batch stored (see
  [Batch reads and writes](BACKENDS.md#batch-reads-and-writes)).
- `increment(key, delta=1, ttl=None)` adds to a counter atomically and returns
  its new value, for quotas and rate limits. It is the backend's `increment()`
  under the manager's prefix: a missing key counts as 0, `ttl` applies when
  the counter is created, and `get()` reads the counter as an `int`. A key
  that holds a value written with `set()` is not a counter and raises
  `CacheXError`.
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
  the HTTP route cache and the locks, so `clear()`/`clear_prefix()` never touch
  unrelated cache entries.
- The prefix is matched as a plain string prefix. A manager with
  `key_prefix="cache:"` therefore also clears the entries of one with
  `key_prefix="cache:users:"`, and an empty `key_prefix` makes `clear()` remove
  everything in the backend, including HTTP responses and locks. Give each manager a prefix that does not start with another's.
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
> server, HTTP responses, locks and other applications' keys included.

### Invalidating a group of values with tags {#group-invalidation-on-memcached}

A value can be written under one or more tags, and every value under a tag
invalidated at once, without knowing their keys:

```python
from fastapi_cachex import AppCache, cached, invalidate_tags


@app.get("/users/{user_id}")
async def read_user(user_id: int, cache: AppCache):
    return await cache.get_or_set(
        f"user:{user_id}",
        lambda: load_user(user_id),
        ttl=300,
        tags=["users", f"user:{user_id}"],
    )


@cached(ttl=300, tags=["users"])
async def count_users() -> int: ...


@app.post("/users/import")
async def import_users():
    await run_import()
    await invalidate_tags("users")  # every value written under "users"
```

`set()`, `get_or_set()` and `@cached` take `tags=`; `@cached` also takes a
function that receives the call's arguments as they were passed and returns
the tags (`tags=lambda user_id: [f"user:{user_id}"]`). `set_many()` and
`add()` take none. `invalidate_tags()` is also a
method of `CacheManager`. The tags are shared with
[`@cache(tags=...)`](HTTP_CACHING.md#by-tag) routes on the same backend, so
one call invalidates cached responses and cached values alike.

How it works, and what follows from it:

- Each tag has a version, kept in the backend under `tag:<name>`. A value
  records the versions of its tags when it is written, and reads as a miss
  once any of them has changed. Nothing is scanned or enumerated, so this
  works on every backend, Memcached included.
- A read of a tagged value costs one extra batched backend read
  (`get_many`), and so does a write; the first write under a tag also stores
  its version. Values without tags pay nothing.
- `has()` is false for an invalidated value, and `add()` replaces it.
- Only a value written with `tags=` is covered. One written before the tags
  were added, or under other names, is served until its TTL: delete such
  keys, or wait one TTL, after adding or renaming tags. (`@cache` routes
  handle this themselves; a plain `get()` cannot know which tags a key should
  have, and `get_or_set()` and `@cached` read through it.)
- A version is created when a value is stored under the tag, not when one is
  looked up: a call that stores nothing leaves nothing behind.
- Invalidated values are not deleted; they leave through their TTL, so give
  tagged values one.
- With `get_or_set()` and `@cached` the versions are read before the value is
  computed, so a value computed from data that changed meanwhile is not
  served after the invalidation.
- A version is kept for 30 days from when it was set, so a tag per user or
  per item does not leave a key behind for good. When it expires, what was
  written under the tag reads as a miss and the next write starts a new
  version: a tagged value lives at most 30 days, whatever its own TTL.
- The same happens if a version is lost earlier (Memcached evicts under
  memory pressure, `flush_all`, a restart, or a `MemoryBackend` with
  `max_entries`, where the `tag:` keys count as entries and can be evicted).
  Losing it can cost a miss, never serve an invalidated value.
- A release before 0.6.1 does not know about tags and serves a tagged value
  until its TTL. Upgrade every worker before relying on `invalidate_tags()`.

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

Values are stored as JSON and read back with `json.loads`, so what you get
back is the JSON-decoded form, not the object you stored. A value `json.dumps`
cannot encode is first converted the way pydantic writes it
(`pydantic_core.to_jsonable_python`); before 0.5.5 it raised `TypeError`:

| Stored | Read back |
|---|---|
| `dict`, `list`, `str`, `int`, `float`, `bool`, `None` | unchanged |
| `tuple` | `list`: `(1, 2)` → `[1, 2]` |
| `dict` with `int`/`float`/`bool`/`None` keys | string keys: `{1: "a"}` → `{"1": "a"}` |
| a pydantic model, a dataclass | `dict`, as `model_dump(mode="json")` gives |
| `datetime`, `date`, `UUID`, `Decimal` | `str`: ISO 8601, `"1.50"` |
| `set`, `frozenset` | `list` |
| `Enum`, `Path`, `timedelta` and the other types pydantic serialises | their JSON form: the enum's value, `"/tmp/x"`, `"PT1M30S"` |
| `bytes`, a generator or other iterator, `SecretStr` (also as a field of a model), your own class | `TypeError`, nothing stored |

`get_or_set()` applies this on a miss too: it encodes the factory's value once,
stores those bytes and returns them decoded, so the first call returns exactly
what later hits return. A value that cannot be encoded raises `TypeError` only
after `factory` has run, since the value doesn't exist before.

### Typed reads {#typed-reads}

To get the object back instead of its JSON form, name the type. `get()`,
`get_or_set()` and `@cached` take `type_=`, anything pydantic's `TypeAdapter`
accepts, and validate the decoded value into it:

```python
from datetime import datetime

from pydantic import BaseModel

from fastapi_cachex import AppCache


class User(BaseModel):
    id: int
    joined: datetime


async def read_user(user_id: int, cache: AppCache) -> User:
    # Stored as JSON; validated back into a User on a hit and on a miss.
    return await cache.get_or_set(
        f"user:{user_id}", lambda: load_user(user_id), ttl=300, type_=User
    )


await cache.set("user:1", user)  # a model is stored as model_dump(mode="json")
await cache.get("user:1")  # {"id": 1, "joined": "2026-01-02T03:04:05Z"}
await cache.get("user:1", type_=User)  # User(id=1, joined=datetime(...))
await cache.get("users", type_=list[User])
```

- A stored value that does not fit the type (written before a field was added,
  say) is treated like an undecodable one: `get()` logs a warning and returns
  the default, and `get_or_set()` runs `factory` again and overwrites it.
- If the value `factory` returns does not fit `type_`, `get_or_set()` raises
  pydantic's `ValidationError`.
- `@cached` uses the function's return annotation when `type_` is not passed,
  so `async def load_user(...) -> User` returns a `User`. `type_=None` returns
  the JSON form instead, as do a function without an annotation and one whose
  annotation the JSON form already has (`dict[str, Any]`, `list[int]`). Before
  0.6.0 the annotation was not read; see
  [Migrating to 0.6.0](MIGRATING_0_6.md#cached-return-annotation).
- A stored `None` does not fit `type_=User`. For a value that may be absent,
  name it: `type_=User | None`.
- A model or dataclass with a `SecretStr` or `SecretBytes` field, at any
  depth, is refused with `TypeError`. pydantic would write the field as
  `"**********"`, and a typed read would then hand back a model whose secret
  is that mask. Exclude the field (`user.model_dump(mode="json",
  exclude={"password"})`) or cache a `dict` you build yourself.
- pydantic's `ValidationError` quotes the value it rejected, so the one raised
  for a `factory` result can put that value into your logs.
- Validation runs on every read. For large values read often, measure before
  choosing a model over the plain `dict`.

The full method list is in the [API reference](api/cache-manager.md).
