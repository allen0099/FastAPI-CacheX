# FastAPI-CacheX Cache Flow

This document explains in detail how FastAPI-CacheX applies its caching logic to
HTTP requests. All behaviour described here lives in
[`fastapi_cachex/cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/cache.py)
unless stated otherwise.

## Overall flow

```
HTTP request arrives
    ↓
@cache decorator intercepts it (only GET goes through the cache; every other
method runs the handler directly and gets no Cache-Control header)
    ↓
Build the cache key: method|||host|||path|||query_params
    ↓
no-store? ── yes → run the handler, neither read nor write the cache,
    │              respond with Cache-Control: no-store
    ↓ no
private? ── yes → run the handler; compare If-None-Match to decide 304 or 200
    │              (the shared backend is neither read nor written)
    ↓ no
Read the backend entry
    ↓
Request carries If-None-Match?
    ├─ and no-cache → run the handler first to compute the current ETag; match → 304
    ├─ otherwise    → compare with the cached entry's ETag; match → 304
    └─ no match / no header → continue
    ↓
Cached entry exists, ttl is set, and no-cache is off?
    ├─ yes → respond with the cached content (including the stored status code
    │        and headers; the handler does **not** run)
    └─ no  → run the handler
              ├─ non-2xx (or 206) → return as-is and **do not write**
              │                     (an existing good entry is not overwritten)
              ├─ streaming/file response → no ETag can be computed; return as-is, do not write
              └─ regular response → set the ETag; write to the backend only if it
                                    differs from the existing entry's ETag
    ↓
Attach Cache-Control to the response (non-2xx responses are returned without it)
```

## Detailed steps

### 1. Request interception and key generation

When a request arrives, the `@cache` decorator does the following:

```python
from fastapi_cachex.types import CACHE_KEY_SEPARATOR  # "|||"

# Cache key format (default_key_builder in fastapi_cachex/cache.py)
cache_key = CACHE_KEY_SEPARATOR.join(
    [request.method, request.headers.get("host", "unknown"), request.url.path, query]
)

# For example:
# GET|||example.com|||/api/users|||page=1&limit=10
# GET|||api.example.com|||/api/users/123|||
```

The separator is `|||` rather than a colon because the host itself may contain a
port (`127.0.0.1:8000`); with a colon the key could not be split reliably, and
`clear_path()` needs to recover the path from the key.

Query parameters are joined in the order the request sent them
(`str(request.query_params)`) and are **not sorted**, so `?page=1&limit=10` and
`?limit=10&page=1` are two separate cache entries. If you want them treated as
one, pass a custom `key_builder` that normalises the query string.

The key format keeps each dimension cached independently:

- **Method isolation**: GET and POST do not share a cache (and currently only GET
  enters the cache flow at all)
- **Host isolation**: `example.com` and `api.example.com` are cached separately
- **Path isolation**: each endpoint has its own entries
- **Query parameter isolation**: different query parameters on the same endpoint
  are cached separately

### 2. Cache-Control directives

The decorator arguments control both the server-side behaviour and the
`Cache-Control` header sent to clients:

```python
# Change how the server-side cache is used
@cache(no_cache=True)     # Always re-run the handler (revalidate); entries are still written
@cache(no_store=True)     # Never read or write the cache

# Normal caching behaviour
@cache(ttl=3600)          # Cache for 1 hour (also used as the max-age value)
@cache(public=True)       # Allow shared caches
@cache(private=True)      # Private only; never touches the shared backend
@cache(immutable=True)    # Content never changes

# Header-only directives (they do not change server-side behaviour)
@cache(ttl=60, must_revalidate=True)                        # must-revalidate
@cache(ttl=60, stale="revalidate", stale_ttl=30)            # stale-while-revalidate=30
@cache(ttl=60, stale="error", stale_ttl=300)                # stale-if-error=300
```

Arguments are validated when the decorator is applied, and a `CacheXError` is
raised if `public` and `private` are both set, or if only one of `stale` /
`stale_ttl` is given.

The header value is built once per decorated route:

| Arguments | `Cache-Control` sent |
|-----------|----------------------|
| `no_store=True` | `no-store` (overrides everything else) |
| `no_cache=True` | `no-cache`, plus `must-revalidate` if requested; `public`/`private`/`max-age`/`stale-*`/`immutable` are omitted |
| anything else | in order: `public` or `private`, `max-age=<ttl>`, `must-revalidate`, `stale-while-revalidate=<n>` or `stale-if-error=<n>`, `immutable` |

> [!NOTE]
> Without `ttl`, an entry is still written (with no expiry) but is never served
> directly: it is only used to answer a matching `If-None-Match` with `304`. Set
> `ttl` to have the server replay cached responses.

> [!WARNING]
> **The default cache key does not include the user's identity**, and the backend
> is shared by every worker and every user. Putting `@cache(ttl=...)` directly on
> an authenticated endpoint will serve user A's response to the next user B who
> requests the same path.
>
> For endpoints whose response depends on the caller, pick one:
>
> 1. `private=True` — never read from or write to the shared backend. It still
>    sends `Cache-Control: private` so the user's own browser can cache the
>    response, and `If-None-Match` is still compared against freshly rendered
>    content.
> 2. A custom `key_builder` that includes the identity — when you really do want
>    a per-user server-side cache.
>
> Take the identity from a trusted source (a verified token claim, a
> dependency-injected user object); do not trust unchecked client headers.

### 3. Cache lookup

The backend is queried with the cache key. It returns a `CacheEntry` (expired
entries are skipped by the backend itself):

```python
from fastapi_cachex.types import CacheEntry

entry = CacheEntry(
    fingerprint='W/"9f86d081..."',  # ETag, a weak validator for the content
    content=b'{"data": "response"}',  # raw response bytes
    media_type="application/json",
    status_code=200,  # replayed with the original status code
    headers={"Vary": "Accept-Encoding"},  # headers sent back on replay
)
```

The TTL is not stored in `CacheEntry`: expiry is the backend's responsibility
(`MemoryBackend` keeps it in `CacheItem.expiry`, Redis uses `SET ... EX`,
Memcached uses the exptime).

If no backend has been configured with `BackendProxy.set()`, the decorator
creates a `MemoryBackend` on the first request and registers it.

**Decision logic** (the `cache.py` wrapper, in order):

```python
if request.method != "GET":
    return await handler()                   # no cache, no Cache-Control

if no_store:
    return await render()                    # no read, no write

if private:
    response, etag = await render()          # shared backend neither read nor written
    return not_modified(...) if etag_matches(client_etag, etag) else response

entry = await backend.get(cache_key)         # expired entries are already skipped here

if client_etag and no_cache:
    fresh = await render()                   # no-cache: always re-render first
    if etag_matches(client_etag, fresh.etag):
        return not_modified(...)             # 304
elif client_etag and entry and etag_matches(client_etag, entry.fingerprint):
    return not_modified(...)                 # 304, handler does not run

if entry and not no_cache and ttl is not None:
    return Response(                         # 200, handler does not run
        content=entry.content,
        status_code=entry.status_code,
        media_type=entry.media_type,
        headers={**(entry.headers or {}), "ETag": entry.fingerprint, ...},
    )

response, body, etag = await render()        # miss (reused if no-cache already rendered)
if not is_cacheable_status(response.status_code):
    return response                          # non-2xx: returned as-is, not written
if etag is None:
    return response                          # streaming/file: no ETag, not written
if not entry or entry.fingerprint != etag:
    await backend.set(cache_key, CacheEntry(...), ttl=ttl)
return response
```

> [!NOTE]
> "Non-2xx is not written" is deliberate: a transient error must not wipe out
> the last good cached response, nor be replayed later as a 200. `206 Partial
> Content` is not cached either, since its body only makes sense for the `Range`
> request that produced it. Non-2xx responses are also never answered with
> `304` and are returned without the decorator's `Cache-Control` header (only
> `no_store=True` adds `no-store` to every response).

### 4. ETag generation and validation

The ETag is computed from the response body and used to detect whether the
content has changed:

```python
# Generation: MD5, marked as a weak validator
def _etag_for(body: bytes) -> str:
    return f'W/"{hashlib.md5(body).hexdigest()}"'
```

`If-None-Match` is evaluated with **weak comparison** as specified by RFC 9110
§8.8.3.2, so:

```
If-None-Match: W/"abc"            → matches "abc" (the W/ prefix is ignored on both sides)
If-None-Match: "abc", W/"def"     → multiple values, compared one by one; any match → 304
If-None-Match: *                  → matches whenever the resource exists → 304
```

A 304 carries the same `Cache-Control` and `ETag` a 200 would, together with the
cache-steering headers `Vary`, `Content-Location` and `Expires`; otherwise an
intermediate cache would lose those fields after revalidation (RFC 9110
§15.4.5).

## Backend storage formats

### MemoryBackend

```python
# dict[str, CacheItem]; CacheItem wraps the CacheEntry and records its expiry
{
    "GET|||example.com|||/api/users|||": CacheItem(
        value=CacheEntry(
            fingerprint='W/"abc123"',
            content=b"...",
            media_type="application/json",
            status_code=200,
            headers=None,
        ),
        expiry=1702650600.5,  # epoch seconds; None means never expires
    ),
}

# Characteristics:
# - Stored in process memory, not shared between processes
# - A background cleanup task sweeps expired items every cleanup_interval
#   seconds (default 60)
# - The cleanup task starts lazily on the first get/set/set_if_absent/increment/
#   get_and_delete call
# - get() deletes an expired item in place and reports a miss, without waiting
#   for the cleanup task
# - Keys are not prefixed
```

### Serialization shared by the network backends ([`backends/codec.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/backends/codec.py))

Redis and Memcached share the same JSON codec; it uses `orjson` when installed
and the standard library `json` otherwise:

```json
{
  "fingerprint": "W/\"abc123\"",
  "content": "<response bytes decoded as latin-1>",
  "media_type": "application/json",
  "status_code": 200,
  "headers": {"Vary": "Accept-Encoding"}
}
```

- `content` uses a **latin-1 round-trip**, not base64: latin-1 maps one-to-one
  onto bytes, so any byte sequence can be placed in JSON text and recovered
  unchanged.
- Entries written by older releases, without the `status_code`/`headers`
  fields, remain readable and decode to `200` with no extra headers.
- Any decode failure (broken JSON, missing fields, wrong types) is treated as a
  **cache miss** and returns `None` instead of raising.
- `increment()` leaves a **bare integer** behind (written by the Redis/Memcached
  INCR family); it decodes to a `CacheEntry` whose fingerprint is `counter`.

### MemcachedBackend

```
key:   "fastapi_cachex:GET|||example.com|||/api/users|||"
value: the JSON document above

# Characteristics:
# - If the namespaced key contains whitespace, control characters or non-ASCII
#   bytes, or exceeds 250 bytes, it is stored under its SHA-256 hex digest
#   instead (`fastapi_cachex:<sha256>`); otherwise Memcached would reject it and
#   the request would fail with a 500
# - A TTL longer than 30 days is sent as an absolute epoch timestamp; otherwise
#   Memcached would read it as a moment in 1970 and expire the entry immediately
# - The protocol cannot enumerate keys, so clear_pattern()/get_all_keys()/
#   get_cache_data() are no-ops that return 0/[]/{} and emit a RuntimeWarning;
#   CacheManager.clear()/clear_prefix() therefore do nothing on this backend
# - clear_path() only deletes a key exactly equal to the given path, so it
#   cannot clear HTTP route entries
# - clear() issues flush_all, which wipes the ENTIRE Memcached server (not just
#   this key prefix) and emits a RuntimeWarning
# - The synchronous pymemcache client runs in worker threads, with connection
#   pooling and default_noreply=False
```

### AsyncRedisCacheBackend

```
key:   "fastapi_cachex:GET|||example.com|||/api/users|||"
value: the JSON document above

# Characteristics:
# - Expiry is set with SET ... EX <ttl> (plain SET when ttl is None)
# - Pattern operations page through keys with SCAN (COUNT=100) instead of KEYS,
#   so the server is never blocked
# - clear() only removes keys under this backend's key prefix
# - get_and_delete() uses GETDEL (requires Redis 6.2+); deletions are sent as
#   batched DELs of up to 100 keys
# - increment() runs a registered Lua script, so incrementing and setting the TTL
#   are one atomic operation
```

> [!NOTE]
> **The TTL fields of the monitoring endpoints are unavailable on the Redis
> backend.** `AsyncRedisCacheBackend.get_cache_data()` returns `(entry, None)`
> for every key without querying each key's actual TTL, so the
> `/cached-hits` and `/cached-records` routes mounted by `add_routes()` show every
> Redis entry as never expiring (`ttl_remaining: null`). Redis still enforces
> expiry itself; the monitoring just cannot see the remaining seconds. On
> Memcached these endpoints return no entries at all.

## Cache clearing strategies

### Automatic clearing

```python
# MemoryBackend: sweeps every cleanup_interval seconds (default 60)
async def cleanup_task():
    while True:
        await asyncio.sleep(self.cleanup_interval)
        # remove every item whose CacheItem.expiry has passed


# The task is only started lazily on the first get/set/set_if_absent/increment/
# get_and_delete call (it needs a running event loop), so write-only usage (for
# example StateManager.create_state) starts it too.

# Redis/Memcached: TTL mechanism
# Use the backend's built-in TTL (SET ... EX, exptime)
# Items expire on their own; no cleanup task is needed
```

### Manual clearing

`clear_path()`, `clear_pattern()`, `clear()` and `invalidate()` are covered in
[HTTP caching](HTTP_CACHING.md#clearing-the-cache).

## Performance

On a cache hit the endpoint handler does not run at all: the cost is one backend
lookup. Which backend to pick is covered in [Backends](BACKENDS.md#choosing-a-backend).

## Cache invalidation scenarios

| Scenario | Behaviour |
|----------|-----------|
| `no_store=True` | The cache is neither read nor written; the endpoint runs every time |
| `no_cache=True` | The endpoint runs every time to recompute the ETag; a match with the client's `If-None-Match` still returns 304, and the cache is updated when the ETag changes |
| `private=True` | The **shared backend** is neither read nor written; `Cache-Control: private` is still sent and the ETag is compared against fresh content |
| No `ttl` | Entries are written without expiry but only used for `If-None-Match` revalidation; the handler runs on every request without a matching validator |
| Cache expired (TTL elapsed) | The endpoint runs again; `MemoryBackend` deletes the expired entry in place when it reads it |
| Non-2xx or 206 response | Returned as-is, not written, and any existing entry is left untouched |
| Streaming/file response | No ETag can be computed; returned as-is and not written |
| Manual `invalidate()` call | The key for that route is deleted |
| Manual `clear_path()` / `clear_pattern()` / `clear()` call | Cleared according to scope (on Memcached, `clear_path()` only deletes an exact key, `clear_pattern()` is a no-op, and `clear()` flushes the whole server) |

## Implementation details

### Cache entry structure

The actual types are dataclasses defined in
[`fastapi_cachex/types.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/types.py):

```python
@dataclass
class CacheEntry:
    fingerprint: str  # ETag, formatted as W/"<md5>"
    content: bytes  # raw response bytes
    media_type: str | None = None
    status_code: int = 200  # replayed as-is
    headers: dict[str, str] | None = None  # sent back on replay


@dataclass
class CacheItem:
    value: CacheEntry
    expiry: float | None = None  # epoch seconds; used by MemoryBackend only
```

`headers` stores the headers the handler set itself, excluding fields that must
be recomputed for every response or must not be replayed: `Set-Cookie`,
`Content-Length`, `Transfer-Encoding`, `Connection`, `Date`, `ETag`,
`Cache-Control` and `Content-Type` (`Content-Type` is restored from
`media_type`; storing both would emit the header twice).

Counters (`backend.increment()`) are also represented as a `CacheEntry`: the
fingerprint is always `counter` and `content` is the decimal value as bytes, so
deletion, clearing and monitoring all treat them the same way.

### Request flow code example

See the decision logic in "3. Cache lookup" above for the decorator's internal
order; on the user side all you need is:

```python
@app.get("/expensive")
@cache(ttl=3600)
async def expensive_endpoint():
    # This function only runs on a cache miss (or when revalidation is needed)
    return await perform_calculation()
```

The handler does not have to declare a `Request` itself: `@cache` injects a
keyword-only parameter named `__cachex_request` into the signature (placed
before `**kwargs` if the handler has one). If the handler **already** declares a
`Request` (including a string annotation, `Annotated[...]`, or a `Request`
subclass), that parameter is reused and nothing is injected.

## FAQ

**Q: Why doesn't a cache hit always return 200?**
A: It depends. If the request carries an `If-None-Match` header whose ETag
matches, a 304 is returned to save bandwidth. Without the header, a 200 with the
content is returned.

**Q: Why aren't POST/PUT responses cached?**
A: `@cache` only applies to GET. Every other method runs the handler directly,
without reading or writing the cache and without adding a `Cache-Control`
header.

**Q: Why are there several cache entries for the same endpoint?**
A: Because the cache key includes the query parameters, and they are **not
sorted**. `/users?page=1` and `/users?page=2` are different entries, and so are
`?a=1&b=2` and `?b=2&a=1`.

**Q: How does MemoryBackend work across multiple processes?**
A: It doesn't. Each process has its own cache; use Redis in production.

**Q: Is clearing the cache synchronous or asynchronous?**
A: Asynchronous: `await cache.clear_path(...)` or `await
cache.clear_pattern(...)`. Note that `clear_pattern()` (as well as
`get_all_keys()` and `CacheManager.clear()`) is a no-op on the Memcached
backend, because the Memcached protocol cannot enumerate keys.
