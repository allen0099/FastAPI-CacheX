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


# Or instantiate directly, e.g. outside of a request:
manager = CacheManager(key_prefix="myapp:", default_ttl=60)
await manager.set("user:42", {"name": "Alice"})
user = await manager.get("user:42")  # {"name": "Alice"}
await manager.delete("user:42")
await manager.clear_prefix()  # clear everything under "myapp:"

# Compute-on-miss: `factory` runs only when the key is missing, expired, or
# undecodable. It may be sync or async.
profile = await manager.get_or_set("user:42", lambda: load_user(42), ttl=300)

# Store only if the key is free: of concurrent callers exactly one gets True.
if await manager.add(f"webhook:{event_id}", True, ttl=86400):
    await deliver_webhook(event_id)

# Glob over this manager's namespace, using the backend's native pattern
# support (Redis SCAN) rather than enumerating every key.
await manager.clear_pattern("user:*")  # matches "myapp:user:*"
```

Complete runnable example: [`examples/app_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/app_cache.py).

## Behavior

- `get()` returns `None` (or a supplied `default=`) on a cache miss — it never
  raises for missing or corrupted entries.
- `set()` lets `TypeError` propagate for values that are not JSON-serializable.
- `get_or_set()` provides no stampede protection: concurrent misses for the same
  key each run `factory`.
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
- The `AppCache` dependency creates and registers a default `CacheManager` the
  first time it is used; `CacheManagerProxy.set()` registers your own instead.

> [!NOTE]
> `clear()`/`clear_prefix()` are implemented via the backend's `get_all_keys()`
> and `delete_many()` (one batched `DEL` on Redis). Since Memcached doesn't
> support key enumeration (see [Backends](BACKENDS.md#memcached)), these
> methods — and `clear_pattern()` — are no-ops on a Memcached backend;
> `get()`/`set()`/`add()`/`delete()`/`has()` work normally. Use Redis or the in-memory
> backend if you need bulk clearing.

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
