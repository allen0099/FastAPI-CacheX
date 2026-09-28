# HTTP Caching

The `@cache` decorator caches the responses of FastAPI GET routes and handles
`Cache-Control`, `ETag` and `If-None-Match` for you. This page covers how to use
it; [Cache flow](CACHE_FLOW.md) explains what happens inside a request.

Complete runnable example: [`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py).

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

`@cache` plays two roles. It writes a `Cache-Control` header for browsers and
intermediaries (CDNs, reverse proxies), and it keeps its own server-side cache in
the backend. Most directives only affect the first: they are written into the
header, and the server-side cache behaves the same with or without them.

| Directive                | Set with                                 | Sent in header     | Effect on the server-side cache                                                                                |
|--------------------------|------------------------------------------|--------------------|----------------------------------------------------------------------------------------------------------------|
| `max-age`                | `ttl=N`                                  | :white_check_mark: | The stored response is served for `N` seconds without running the handler (`ttl=0` or unset: nothing is stored). |
| `no-cache`               | `no_cache=True`                          | :white_check_mark: | The handler runs on every request; the response is still stored, and a matching `If-None-Match` gets a 304.   |
| `no-store`               | `no_store=True`                          | :white_check_mark: | Nothing is read or stored, and no ETag is set.                                                                  |
| `private`                | `private=True`                           | :white_check_mark: | The backend is bypassed; the handler runs on every request, and ETag revalidation still works.                 |
| `public`                 | `public=True`                            | :white_check_mark: | Requests with `Authorization` or a session still use the backend (otherwise they bypass it).                   |
| `immutable`              | `immutable=True`                         | :white_check_mark: | None (header only).                                                                                            |
| `must-revalidate`        | `must_revalidate=True`                   | :white_check_mark: | None (header only).                                                                                            |
| `stale-while-revalidate` | `stale="revalidate", stale_ttl=N`        | :white_check_mark: | None (header only): the server-side cache never serves stale content.                                         |
| `stale-if-error`         | `stale="error", stale_ttl=N`             | :white_check_mark: | None (header only): a failing handler is not answered from the cache.                                          |
| `s-maxage`               | —                                        | :x:                | —                                                                                                              |
| `proxy-revalidate`       | —                                        | :x:                | —                                                                                                              |
| `no-transform`           | —                                        | :x:                | —                                                                                                              |
| `must-understand`        | —                                        | :x:                | —                                                                                                              |

`no_cache=True` and `no_store=True` replace the rest of the header: with
`no_cache` only `no-cache` (and `must-revalidate`, when set) is sent, and with
`no_store` only `no-store`. How the other arguments combine is described in
[Cache flow](CACHE_FLOW.md#2-cache-control-directives).

### The request's `Cache-Control` is ignored

A client's own `Cache-Control` request header (`no-cache`, `max-age=0`, as sent
by a browser's hard reload, and so on) does not change what `@cache` does. This
is deliberate: if a request header could bypass the cache, any client could
send every request straight to your handler. Conditional requests are honoured:
a matching `If-None-Match` gets a 304.

## Cache hit behavior

When a cached entry is valid (within TTL):

- **Default behavior**: Returns the cached content directly, with the status code and headers the handler originally produced, without re-executing the endpoint handler
- **With `If-None-Match` header**: Returns HTTP 304 Not Modified if the ETag matches
- **With `no-cache` directive**: Forces revalidation with fresh content before deciding on 304
- **With `private=True`**: Nothing is read from or written to the shared backend; the handler runs every time and only `If-None-Match` revalidation applies
- **Without `ttl`** (`ttl=None`): Nothing is read from or written to the backend, as with `private=True`. The handler runs on every request, and `If-None-Match` gets a 304 only when it matches the freshly rendered response, so an old ETag never gets a 304 once the content has changed
- **With `ttl=0`**: Sends `max-age=0` and otherwise behaves like `ttl=None`. A negative `ttl`, a non-`int` one (such as `1.5` or `True`) and one above `MAX_TTL` (see [TTL values](BACKENDS.md#ttl-values)) are rejected with `CacheXError` when the decorator is applied

Only successful responses are stored. A response the handler *returns* with a
non-2xx status (for example `Response(..., status_code=404)`) is passed straight
through and never cached, so a transient error cannot replace or poison the last
good entry. `206 Partial Content` is excluded as well, since its body is only
meaningful for the `Range` request that produced it.

A response that belongs to one caller is never stored either (#296):

- **The request carries `Authorization` or a session.** As RFC 9111 §3.5 requires of a
  shared cache, the backend is bypassed, as with `private=True`: nothing is
  read or written, the handler runs, and `If-None-Match` is compared against
  the fresh render. The response (and a 304) is sent with `private` in place
  of `public`, keeping the decorator's other directives (`private, no-cache`
  on a `no_cache` route), so a CDN or proxy does not store it either.
  `public=True` routes are exempt, and so are routes with
  `cache_authorized=True`, the opt-in for a key builder that includes the
  caller's identity (see [Authenticated endpoints](#authenticated-endpoints)).
  `must_revalidate=True` does not lift the bypass: RFC 9111 would let a shared
  cache reuse such a response under `must-revalidate`, but the library
  requires an explicit opt-in.
  A request has a session when `FastAPICacheXSessionMiddleware` (or the
  deprecated `SessionMiddleware`) loaded one for it, from the token header, a
  bearer token or the session cookie, with or without a user, or when
  `request.session` is non-empty under any session middleware, Starlette's
  included. A token that resolves to no session (forged, expired) does not
  count, so it cannot be used to skip the cache. Before 0.3.9 only
  `Authorization` did, and a plain `@cache` on a route that read the session
  served one visitor's response to the next (#319).
- **The handler's own `Cache-Control` contains `private` or `no-store`**
  (as whole directives, in any case). The response is served but not stored,
  and the handler's header is sent unchanged instead of the decorator's.
- **The response sets a cookie.** It is served, `Set-Cookie` included, but not
  stored, and it (and a 304) is sent with `private` in place of `public`,
  keeping the other directives, so a shared cache downstream does not store it
  either.

In the last two cases an entry already stored under the key is left alone, and
a request that finds a valid entry is still answered from it before the
handler runs. A handler's own `private`/`no-store` header always wins, and
`no_store=True` still sends only `no-store`. Each skip is logged at `DEBUG`.

A handler that returns plain data instead of a `Response` gets the same
treatment it would without `@cache`: the value is validated and filtered by the
route's response model (declared or inferred from the return annotation, with
the `response_model_*` options), the route's `status_code` applies, and the
status and headers set on an injected `response: Response` parameter are kept.

### When the backend fails

`@cache` fails open. If the backend raises while reading, for example because
Redis or Memcached is unreachable, the request is treated as a cache miss and
the handler runs. If storing the response raises, for example because it is
larger than Memcached's item size limit (1 MB by default), the response is
served unstored. Either way a warning is logged on the `fastapi_cachex.cache`
logger, and a backend outage cannot turn cached routes into 500s. The load
goes to your handlers instead, so watch for those warnings.

The warning names the request's method and path and a `key_ref`, a short
SHA-256 digest of the cache key, but not the key itself: the key holds the
raw query string, `vary` header values and any `build_cache_key` components,
which may be tokens or personal data. The full key is logged at `DEBUG` with
the same `key_ref`, so enabling `DEBUG` on `fastapi_cachex.cache` while
troubleshooting ties a warning to its key.

Pass `fail_open=False` to let the backend error propagate and fail the request
instead:

```python
@app.get("/report")
@cache(ttl=300, fail_open=False)
async def report():
    return await build_report()
```

This only covers `@cache`. `invalidate()`, `CacheManager`, `StateManager`,
`CacheLock` and sessions still raise backend errors to the caller.

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

The host and path come from the client, so `|` and `%` in them are percent-encoded
(`%7C` and `%25`). A `Host` header or path containing `|||` therefore cannot shift
the components and make one request's key equal another's. The query string is
URL-encoded already. `clear_path()` takes the path as your application sees it
(`request.url.path`) and encodes it the same way; `clear_pattern()` matches the
stored key, so write `%7C` there for a `|`. Before 0.3.8 both were stored as sent,
so after upgrading, entries for a host or path containing `|` or `%` are cached
afresh once.

The host is still whatever the client sends. Unless a reverse proxy or load
balancer in front of the app already rejects unknown hosts, add Starlette's
`TrustedHostMiddleware`, so a forged `Host` gets a `400` instead of filling the
cache with entries no one else will request:

```python
from starlette.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["example.com", "*.example.com"]
)
```

### Adding components to the key

A custom `key_builder` that needs one more dimension (a user ID, a tenant, a
locale) should call `build_cache_key(request, *components)` rather than
rebuilding the format by hand. With no components it returns exactly the
default key; each component is appended after the query string:

```
{method}|||{host}|||{path}|||{query_params}|||{component}|||...
```

```python
from fastapi import Request

from fastapi_cachex import build_cache_key


def per_tenant_key(request: Request) -> str:
    return build_cache_key(request, request.state.tenant_id)
```

Components are `str` or `int` (an `int` is written in decimal, so `1` and `"1"`
are the same component); anything else, `None` included, raises `TypeError`, so
a missing ID cannot quietly put every such caller under one `"None"` key. Each
component is percent-encoded like the host and path, so a value containing
`|||` cannot shift the components. An empty string is still a component:
`build_cache_key(request, "")` is not the default key.

Because the path stays the third component, `clear_path()` still finds these
keys: without `include_params` it clears every entry for the path with an empty
query string whatever its extra components, and with it every entry for the
path. The monitoring routes show the extra components, decoded, in
`extra_components`. `default_key_builder(request)` is `build_cache_key(request)`.

The Redis and Memcached backends also put their own prefix (`fastapi_cachex:` by
default) in front of every key, so other applications can share the server;
`MemoryBackend` has no prefix. `CacheManager` (see
[Application cache](APP_CACHE.md)) uses a separate, simpler `cache:`-prefixed key
namespace instead of this `|||`-separated format, since its keys aren't tied to
HTTP requests.

### Varying on request headers

The key includes no request header, so a route whose response depends on, say,
`Accept-Language` would serve the first cached language to everyone. List such
headers in `vary`:

```python
@app.get("/greeting")
@cache(ttl=300, vary=["Accept-Language"])
async def greeting(request: Request):
    return {"text": translate("hello", request.headers.get("accept-language"))}
```

Each listed header adds a `name=value` component to the key: the name
lower-cased, the value trimmed (repeated header lines joined with `,`), and a
missing header treated as an empty one. The components are escaped like the
rest of the key and come after whatever the `key_builder` returns, so `vary`
and a custom key builder compose:
`GET|||example.com|||/greeting|||||||tenant-1|||accept-language=de` for
`key_builder` returning `build_cache_key(request, "tenant-1")`. Routes without
`vary` keep their keys.

The names are also added to the `Vary` header of every response to a GET
request on the route, on a 200 or a 304, served from the backend or not
(`private`, `no_store`, a bypassed `Authorization` request or a response that
is not stored), so a shared cache in front of the app keys on them too. A name
the response already lists (in any case) is not repeated, and a response with
`Vary: *` is left alone.

`vary` must be a list (or tuple) of header field names. A bare string such as
`vary="Accept"` is rejected when the decorator is applied, as are empty names,
`*` and anything that is not a valid field name.

#### Credential headers are hashed

The key is not secret: it is listed by `get_all_keys()`, shown by the
`/cached-records` and `/cached-hits` monitoring routes, and stored as-is in the
Redis or Memcached keyspace. So for the headers that carry credentials,
`Authorization`, `Proxy-Authorization`, `Cookie` and `X-Session-Token` (the
session subsystem's default `header_name`), matched in any case, the component
holds the full hex SHA-256 of the value (trimmed and joined as above) instead
of the value:

```
GET|||example.com|||/me|||||||authorization=sha256:3f0a…(64 hex digits)
```

The same token always gives the same digest, so it hits its own entry, and two
tokens give two entries. A missing or empty credential header is not hashed:
it stays `authorization=`, like any other empty header, so every anonymous
caller shares one entry and the key still shows that it is the anonymous one.
Every other header, including a session header configured under another name,
stays readable; if yours carries a secret, key on it through a `key_builder`
(hashing it yourself) rather than `vary`.

`vary=["Authorization"]` does not lift the rule for authorized requests (see
[Authenticated endpoints](#authenticated-endpoints)): a request with an
`Authorization` header (or a session) still bypasses the backend unless the route is
`public=True` or passes `cache_authorized=True`. Without either, only the
anonymous `authorization=` entry is ever stored.

#### `vary=["Cookie"]` warns

Listing `Cookie` keys on the whole `Cookie` header, so every visitor with a
distinct set of cookies (a session ID, an analytics ID, a consent flag) gets
their own entry, and a new one whenever any cookie changes: the number of
entries grows with the number of visitors. `@cache` emits a `UserWarning` when
the decorator is applied, pointing at your `@cache(...)` line. Usually one of
these is what you want instead:

- a `key_builder` returning `build_cache_key(request, <the one cookie or the
  user id that matters>)`, plus `Vary: Cookie` set on the response yourself;
- `private=True`, which leaves per-visitor responses to the browser cache.

The per-caller rules still apply: a request carrying a cookie is cached (only
`Authorization` or a session triggers the bypass), but a response that sets a cookie is
never stored and is sent with `private`, so a route that refreshes a session
cookie on every request stores nothing. If you do want `vary=["Cookie"]`,
silence the warning with the standard filter, before the module defining the
route is imported:

```python
import warnings

warnings.filterwarnings("ignore", message="cache vary on Cookie")
```

`Authorization` and `X-Session-Token` in `vary` do not warn: they are also one
entry per caller, but that is what `vary` with `cache_authorized=True` is for,
and a caller keeps the same token across many requests, unlike an arbitrary
bundle of cookies.

> [!WARNING]
> **Every distinct header value is its own entry, and the values come from the
> client.** `Accept-Language: de`, `de-DE`, `de-DE,de;q=0.9` and every other
> spelling are separate keys, and a client can send a new one on every request
> to fill the backend. Each listed header multiplies the number of entries.
> When only a few values matter, normalise in a `key_builder` instead, and add
> the header to `Vary` yourself:
>
> ```python
> SUPPORTED = ("en", "de", "fr")
>
>
> def locale_key(request: Request) -> str:
>     wanted = request.headers.get("accept-language", "")
>     locale = next(
>         (tag for tag in SUPPORTED if wanted.lower().startswith(tag)), "en"
>     )
>     return build_cache_key(request, locale)
>
>
> @app.get("/greeting")
> @cache(ttl=300, key_builder=locale_key)
> async def greeting(request: Request, response: Response):
>     response.headers["Vary"] = "Accept-Language"
>     return {"text": translate("hello", locale_of(request))}
> ```

`clear_path()` clears every variant of a path (see
[Adding components to the key](#adding-components-to-the-key)).
`invalidate()` takes the same `vary` list and deletes only the variant the
request it is given selects.

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
>    bypasses the backend, so the key builder would never be used. Pass
>    `cache_authorized=True` when callers authenticate with an `Authorization`
>    header or a session: without it such requests bypass the backend too.
>
> If callers authenticate with a cookie of your own rather than the library's
> session, nothing triggers the bypass: a plain `@cache` serves the first
> caller's response to everyone, so use one of the two options above.

```python
from fastapi import Request, Response

from fastapi_cachex import build_cache_key
from fastapi_cachex import cache


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
    # The default key plus the user ID, escaped like the host and path.
    return build_cache_key(request, user_id)


@app.get("/me/dashboard")
@cache(ttl=60, key_builder=per_user_key, cache_authorized=True)
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

The key builder runs only when `@cache` reads or writes the backend, so it is not
called for `no_store=True`, `private=True`, routes without a `ttl`, or requests
with `Authorization` or a session on a route without `public=True` or `cache_authorized=True`. Before 0.3.8
it was, only to feed a debug log. Keep it free of side effects.

## Clearing the cache

### By path or pattern

The clearing methods live on the backend, which you can inject with the
`CacheBackend` dependency or fetch with `BackendProxy.get()`. With no backend
configured, `CacheBackend` registers the same `MemoryBackend` fallback that
`@cache` would, so it works before any cached route has run (before 0.3.8 it
answered `500` until then); `BackendProxy.get()` still raises
`BackendNotFoundError`.

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

On Memcached, which cannot enumerate keys, `clear_path()` cannot find HTTP
entries at all: it deletes only a key named exactly as the path and emits a
`RuntimeWarning` on every call. Use `invalidate()` (below) there instead.

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

`invalidate(request, key_builder=None, vary=None)` returns `True` when an entry existed and
was removed, `False` otherwise, including when no backend is configured. An
error from the backend itself is raised to the caller (see
[When the backend fails](#when-the-backend-fails)). The request you hand it must produce the cached route's key:
same method, host, path and query string. If the cached route uses a custom
`key_builder` or `vary`, pass the same here, or the key will not match; with
`vary` only the variant selected by the request's own header values is
deleted, and `clear_path()` removes all of them.

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
- `GET {prefix}/cached-records` — every cached record with its size, expiry,
  `media_type` (the stored response's media type, `null` if it had none) and a
  preview of the first 100 bytes of the cached content. With
  `include_content_preview=False`, `content_preview` is `null` and no response
  body leaves the server; keys, sizes and expiry are still reported.
  `content_type` is always `"bytes"` and is kept for compatibility; read
  `media_type` instead.

> [!WARNING]
> **These routes have no authentication of their own.** `include_in_schema=False`
> only hides them from the OpenAPI document; anyone who guesses the path can read
> them. `/cached-records` includes a preview of the cached content (unless
> `include_content_preview=False`) and exposes your whole route structure. In
> production always pass `dependencies=[Depends(your_auth)]`, or mount them on
> an internal-only app.
>
> Calling `add_routes()` without `dependencies` emits a `UserWarning`. Version
> 0.4.0 will require the parameter and turn `include_content_preview` off by
> default ([#298](https://github.com/allen0099/FastAPI-CacheX/issues/298)). For
> a local or test app that should stay open, pass `dependencies=[]` to opt out
> deliberately without the warning.

> [!NOTE]
> On Memcached, which cannot enumerate keys, both routes return nothing.
