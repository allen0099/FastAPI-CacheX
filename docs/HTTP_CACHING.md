# HTTP Caching

The `@cache` decorator caches the responses of FastAPI GET routes and handles
`Cache-Control`, `ETag` and `If-None-Match` for you. This page covers how to use
it; [Cache flow](CACHE_FLOW.md) explains what happens inside a request.

## The `@cache` decorator

```python
from fastapi import FastAPI
from fastapi_cachex import cache

app = FastAPI()


@app.get("/")
@cache(ttl=60)  # Cache for 60 seconds
async def read_root():
    return {"Hello": "World"}


@app.get("/no-cache")
@cache(no_cache=True)  # Always revalidate: the handler runs on every request
async def non_cache_endpoint():
    return {"Hello": "World"}


@app.get("/no-store")
@cache(no_store=True)  # Never store the response anywhere
async def non_store_endpoint():
    return {"Hello": "World"}
```

Only GET requests are cached; other methods run the handler as usual. The
handler does not need to declare a `Request` parameter — the decorator adds one
when it is missing. If no backend has been configured, `@cache` falls back to a
`MemoryBackend` (see [Backends](BACKENDS.md)).

## Cache-Control directives

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

How each decorator argument turns into the header is described in
[Cache flow](CACHE_FLOW.md#2-cache-control-directives).

## Cache hit behavior

When a cached entry is valid (within TTL):

- **Default behavior**: Returns the cached content directly, with the status code and headers the handler originally produced, without re-executing the endpoint handler
- **With `If-None-Match` header**: Returns HTTP 304 Not Modified if the ETag matches
- **With `no-cache` directive**: Forces revalidation with fresh content before deciding on 304
- **With `private=True`**: Nothing is read from or written to the shared backend; the handler runs every time and only `If-None-Match` revalidation applies
- **Without `ttl`** (`ttl=None`): The cached body is never served directly; the handler runs on every request except one whose `If-None-Match` matches the stored ETag, which gets a 304
- **With `ttl=0`**: Sends `max-age=0` and otherwise behaves like `ttl=None`. A negative `ttl` is rejected with `CacheXError` when the decorator is applied

Only successful responses are stored. A response the handler *returns* with a
non-2xx status (for example `Response(..., status_code=404)`) is passed straight
through and never cached, so a transient error cannot replace or poison the last
good entry. `206 Partial Content` is excluded as well, since its body is only
meaningful for the `Range` request that produced it. `Set-Cookie` is never
stored or replayed.

A handler that returns plain data instead of a `Response` gets the same
treatment it would without `@cache`: the value is validated and filtered by the
route's response model (declared or inferred from the return annotation, with
the `response_model_*` options), the route's `status_code` applies, and the
status and headers set on an injected `response: Response` parameter are kept.

## Cache keys

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

All backends automatically namespace keys with a prefix (e.g., `fastapi_cachex:`)
to avoid conflicts with other applications. `CacheManager` (see
[Application cache](APP_CACHE.md)) uses a separate, simpler `cache:`-prefixed key
namespace instead of this `|||`-separated format, since its keys aren't tied to
HTTP requests.

### Authenticated endpoints

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
>    do want a server-side cache per user. Leave `private` unset: `private=True`
>    bypasses the backend, so the key builder would never be used.

```python
from fastapi import Request, Response

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
@cache(ttl=60, key_builder=per_user_key)
async def my_dashboard(user: CurrentUser, response: Response):
    # Without `private`, the response goes out as `Cache-Control: max-age=60`,
    # which a shared cache (CDN, reverse proxy) may store. Vary on whatever
    # carries the identity so such a cache keeps one copy per user.
    response.headers["Vary"] = "Authorization"
    return build_dashboard(user)
```

A per-user entry is only safe from shared caches in front of your app if they
honour `Vary` for that header. When they don't, or when identity comes from
something a shared cache cannot see, use option 1 instead.

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

## Clearing the cache

### By path or pattern

The clearing methods live on the backend, which you can inject with the
`CacheBackend` dependency or fetch with `BackendProxy.get()`:

```python
from fastapi_cachex import CacheBackend


@app.post("/admin/clear")
async def clear(cache: CacheBackend) -> None:
    # Clear a specific path: only entries WITHOUT query params...
    await cache.clear_path("/api/users")
    # ...or every query-param variant too
    await cache.clear_path("/api/users", include_params=True)

    # Clear by pattern: matched against the whole key method|||host|||path|||query
    await cache.clear_pattern("GET|||*|||/api/users/*")
    # Keys you built yourself (e.g. CacheManager keys) match directly
    await cache.clear_pattern("cache:user:*")

    # Clear everything
    await cache.clear()  # removes every cache entry
```

`clear_path()` matches entries for the path across every method and host. A
pattern written as a bare path (for example `clear_pattern("/api/users/*")`)
cannot match an HTTP key; when such a call clears nothing it emits a
`RuntimeWarning` pointing you to `clear_path()`.

What each backend supports is listed under [Backends](BACKENDS.md).

### Invalidating a single cached route

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

## Monitoring routes

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
    include_content_preview=False,  # default True: show the first 100 bytes
)
```

- `GET {prefix}/cached-hits` — every cached entry split into method, host, path
  and query, with its ETag and expiry, plus counts of valid and expired entries
  and the distinct cached paths. It does not count hits.
- `GET {prefix}/cached-records` — every cached record with its size, expiry and
  a preview of the first 100 bytes of the cached content. With
  `include_content_preview=False`, `content_preview` is `null` and no response
  body leaves the server; keys, sizes and expiry are still reported.

> [!WARNING]
> **These routes have no authentication of their own.** `include_in_schema=False`
> only hides them from the OpenAPI document; anyone who guesses the path can read
> them. `/cached-records` includes a preview of the cached content (unless
> `include_content_preview=False`) and exposes your whole route structure. In
> production always pass `dependencies=[Depends(your_auth)]`, or mount them on
> an internal-only app.

> [!NOTE]
> On Memcached, which cannot enumerate keys, both routes return nothing.
