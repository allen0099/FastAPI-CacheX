# HTTP Caching

The `@cache` decorator caches the responses of FastAPI GET routes and handles
`Cache-Control`, `ETag` and `If-None-Match` for you. This page covers how to use
it; [Cache flow](CACHE_FLOW.md) explains what happens inside a request.

Complete runnable example: [`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py).

## The `@cache` decorator

<!-- fmt:off -->
```python
--8<-- "examples/http_cache.py:routes"
```
<!-- fmt:on -->

`ttl=60` serves a stored response for 60 seconds, `no_cache=True` makes clients
revalidate every time, and `private=True` keeps a response out of the shared
backend. `no_store=True` keeps it out of every cache; all options are listed
under [Cache-Control directives](#cache-control-directives).

Only GET requests are cached; other methods run the handler as usual. The
handler does not need to declare a `Request` parameter — the decorator adds one
when it is missing. If no backend has been configured, `@cache` falls back to a
`MemoryBackend` and logs a warning once per process (see
[Backends](BACKENDS.md#in-memory-default)).

### Decorator order

`@cache` goes **below** the route decorator (`@app.get(...)`,
`@router.get(...)`), directly above the function. Python applies decorators
bottom-up, and FastAPI registers whatever function reaches the route decorator.
With `@cache` on top, FastAPI registers the undecorated handler and never calls
the cache wrapper: the route still works, but nothing is cached, no
`Cache-Control` or `ETag` header is sent, and nothing warns.

```python
# ✅ Cached: FastAPI registers the @cache wrapper.
@app.get("/items")
@cache(ttl=60)
async def items(): ...


# ❌ Not cached: FastAPI registers the plain function; the wrapper is never called.
@cache(ttl=60)
@app.get("/items")
async def items(): ...
```

The same applies to `app.add_api_route(path, cache(ttl=60)(handler))`: pass the
decorated function, not the plain one.

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

`no_store=True` together with any other caching argument (`ttl`, `stale`,
`no_cache`, `public`, `private`, `immutable`, `must_revalidate`) emits a
`UserWarning` pointing at your `@cache(...)` line when the decorator is
applied: `no_store` overrides them, and the warning names them.

A bare `@cache()`, with neither `ttl` nor a directive, stores nothing and has no
`Cache-Control` of its own. It adds an ETag and answers a matching
`If-None-Match` with `304`, and keeps the handler's own `Cache-Control` (or
sends none). As on every route, a response that sets a cookie or answers a
request with `Authorization` or a session is still sent with `private`.

### The request's `Cache-Control` is ignored

A client's own `Cache-Control` request header (`no-cache`, `max-age=0`, as sent
by a browser's hard reload, and so on) does not change what `@cache` does. This
is deliberate: if a request header could bypass the cache, any client could
send every request straight to your handler. Conditional requests are honoured:
a matching `If-None-Match` gets a 304.

## Cache hit behavior

When a cached entry is valid (within TTL):

- **Default behavior**: Returns the cached content directly, with the status code and headers the handler originally produced (every line of a header sent more than once), without re-executing the endpoint handler
- **With `If-None-Match` header**: Returns HTTP 304 Not Modified if the ETag matches
- **With `no-cache` directive**: Forces revalidation with fresh content before deciding on 304
- **With `private=True`**: Nothing is read from or written to the shared backend; the handler runs every time and only `If-None-Match` revalidation applies
- **Without `ttl`** (`ttl=None`): Nothing is read from or written to the backend, as with `private=True`. The handler runs on every request, and `If-None-Match` gets a 304 only when it matches the freshly rendered response, so an old ETag never gets a 304 once the content has changed
- **With `ttl=0`**: Sends `max-age=0` and otherwise behaves like `ttl=None`. A negative `ttl`, a non-`int` one (such as `1.5` or `True`) and one above `MAX_TTL` (see [TTL values](BACKENDS.md#ttl-values)) are rejected with `CacheXError` when the decorator is applied

### The `Age` header

A response served from a stored entry carries an `Age` header: the whole
number of seconds since `@cache` stored it (RFC 9111 §5.1). That covers a
cache hit and a 304 answered from the stored entry's ETag. `Cache-Control`
still says `max-age=<ttl>`, and a browser or CDN subtracts `Age` from it
(RFC 9111 §4.2.3), so a response stored 50 seconds into a 60-second ttl is
reused downstream for at most 10 more seconds. Without `Age`, a hit just
before the entry expired restarted the downstream clock, and the content could
be reused for up to twice the ttl.

```
GET /items  → 200, Cache-Control: max-age=60, no Age (the handler ran)
GET /items  → 200, Cache-Control: max-age=60, Age: 42 (served 42 s after it was stored)
```

The time an entry was stored comes from the wall clock of the process that
stored it and is read by whichever process serves it, so `Age` is clamped to
`0`–`ttl` in case two hosts' clocks disagree. No `Age` is sent when the handler
runs (a miss, `no_cache=True`, a bypassed request) or for an entry stored by
a release before 0.3.9, which does not record the time. An `Age` header the
handler sets itself is not stored.

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
  `public=True` routes are exempt. Routes with `cache_authorized=True`, the
  opt-in for a key builder that includes the caller's identity (see
  [Authenticated endpoints](#authenticated-endpoints)), read and write the
  backend for such requests but still answer them with `private`: the entry is
  per caller only in the backend, while a CDN keys on the URL alone (before
  0.3.9 they sent the decorator's header unchanged, #372).
  `must_revalidate=True` does not lift the bypass: RFC 9111 would let a shared
  cache reuse such a response under `must-revalidate`, but the library
  requires an explicit opt-in. A route without a positive `ttl` skips the
  backend anyway, but its response to such a request still gets `private`
  (before 0.3.9 it was sent without it, #362); `private=True` routes send it
  already.
  A request has a session when `FastAPICacheXSessionMiddleware` (deprecated,
  removed in 0.5.0) loaded one for it, from the token header, a
  bearer token or the session cookie, with or without a user, or when
  `request.session` is non-empty under any session middleware, Starlette's
  included. A token that resolves to no session (forged, expired) does not
  count, so it cannot be used to skip the cache. Before 0.3.9 only
  `Authorization` did, and a plain `@cache` on a route that read the session
  served one visitor's response to the next (#319). The first bypass on each
  route that reads the backend is logged at `WARNING` (see
  [Requests with credentials](#requests-with-credentials)).
- **The handler's own `Cache-Control` contains `private` or `no-store`**
  (as whole directives, in any case). The response is served but not stored,
  and the handler's header is sent unchanged instead of the decorator's.
- **The response sets a cookie.** It is served, `Set-Cookie` included, but not
  stored, and it (and a 304) is sent with `private` in place of `public`,
  keeping the other directives, so a shared cache downstream does not store it
  either. A 304 for a fresh render carries the `Set-Cookie` lines too, and the
  handler's background task still runs (before 0.4.1 the 304s on bypassed and
  `no_cache` requests dropped both, #233).

In the last two cases an entry already stored under the key is left alone, and
a request that finds a valid entry is still answered from it before the
handler runs. A handler's own `private`/`no-store` header always wins, and
`no_store=True` still sends only `no-store`. Each skip is logged at `DEBUG`.

A handler that returns plain data instead of a `Response` gets the same
treatment it would without `@cache`: the value is validated and filtered by the
route's response model (declared or inferred from the return annotation, with
the `response_model_*` options), the route's `status_code` applies, and the
status and headers set on an injected `response: Response` parameter are kept.

Headers and cookies that dependencies set on FastAPI's shared `Response` reach
the client with this request's values, on a miss, a hit and a 304 alike, whether
or not the handler declares `response: Response`. They are never stored with
the entry, so a header such as `X-RateLimit-Remaining` is not replayed from the
request that filled the cache. A dependency's `Cache-Control` with `private`
or `no-store`, and a cookie a dependency sets, count as the handler's own: the
response is not stored, and it is sent with the dependency's header or with
`private`, a hit or 304 included. A dependency's other `Cache-Control` is
treated like the handler's own: the decorator's replaces it, and a bare
`@cache()` keeps it. A status code a dependency sets is sent but keeps the
response out of the backend, since it may hold for that request only; a hit
keeps the stored status. Lines the handler itself adds to `response`, and the
status code it sets, belong to the stored response; where the handler sets a
header a dependency also set, the handler's value is sent, on a hit as well. A
handler that deletes a dependency's header removes it only when it runs. FastAPI
itself merges these lines only when the handler returns plain data; `@cache`
adds the dependencies' lines to a handler's own `Response` too. Before 0.4.1, a
handler without a `response: Response` parameter lost them, and one with it
replayed the headers of the request that filled the cache (#233).

> [!NOTE]
> A dependency that sets a cookie on every request, such as an app-wide CSRF or
> session-refresh dependency, therefore keeps every `@cache` route it applies to
> out of the backend. Limit it to the routes that need it, or set the cookie
> only when it changes.

### Requests with credentials

A single-page app that sends `Authorization` on every request, or a site where
every visitor has a session, gets no cache hits at all on a plain `@cache`
route: each request bypasses the backend (see above). Pick the option that
matches what the handler returns:

- **The response is the same for every user** (a product list, a public
  article): set `public=True`. Requests with `Authorization` or a session then
  read and write the backend like any other. Note that `public=True` also
  changes the header sent downstream to `Cache-Control: public, ...`, which
  tells a CDN or reverse proxy that it may store the response even though the
  request carried credentials. Only use it when that is true.
- **The response is per user** (a profile, a cart, a dashboard): set
  `cache_authorized=True` together with a `key_builder` that puts the verified
  caller's identity into the key, so each user gets their own entry (see
  [Authenticated endpoints](#authenticated-endpoints)). Without the identity in
  the key, one user's response is served to the next. The response still goes
  out with `private`, so downstream only the user's browser keeps a copy. If you do not need a
  server-side cache for it, leave both options unset (or use `private=True`)
  and let only the browser cache it.

So that a 0% hit rate does not go unnoticed, the first request that bypasses
a route because of a credential is logged once at `WARNING` on the
`fastapi_cachex.cache` logger, naming the route template (such as
`'/items/{item_id}'`), the credential that caused it (an `Authorization`
header, a session token, or non-empty `request.session` data) and the two
options above:

```text
@cache bypassed the shared backend for route '/products': the request carried an Authorization header, so the response is not cached and is sent with Cache-Control: private. If the response is the same for every user, set @cache(public=True) (this also sends Cache-Control: public, so shared caches downstream may store it). If it is per user, set cache_authorized=True with a key_builder that puts the verified caller's identity into the key. Logged once per route and credential; each bypass is logged at DEBUG.
```

The warning is logged once per route and credential kind for the life of the
process (so once per worker), never with a header value or token. Every
bypass is still logged at `DEBUG`. When the bypass is what you want, set
`private=True` on the route, which bypasses the backend without a warning, or
raise the logger's level:

```python
import logging

logging.getLogger("fastapi_cachex.cache").setLevel(logging.ERROR)
```

That also hides the backend-failure warnings described below, so prefer
`private=True` where it fits.

### When the backend fails

`@cache` fails open. If the backend raises while reading, for example because
Redis or Memcached is unreachable, the request is treated as a cache miss and
the handler runs. If storing the response raises, for example because it is
larger than Memcached's item size limit (1 MB by default), the response is
served unstored. Either way a warning is logged on the `fastapi_cachex.cache`
logger, and a backend outage cannot turn cached routes into 500s. The load
goes to your handlers instead, so watch for those warnings.

Failing open is only as fast as the backend's error. By default redis-py 8
retries a failed Redis command 10 times with exponential backoff, so while
Redis refuses connections each read and each write takes about 3 to 4 s to
fail, and a cached request, which does both, about 7 s. When the Redis host
does not answer at all, every attempt also waits out the connect timeout, and a
request can take about 30 s. To fail within the timeouts, turn the retries off
and shorten the timeouts through the backend's keyword arguments:

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

See [Failing fast when Redis is down](BACKENDS.md#failing-fast-when-redis-is-down)
for the trade-offs.

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

This only covers `@cache`. `invalidate()`, `CacheManager`, `CacheLock` and the
deprecated `StateManager` and sessions still raise backend errors to the caller.

## Cache keys

Cache keys are generated in the following format to avoid collisions:

```
http:v2|{method}|{host}|{path}|{query_params}
```

`http:v2` is the format tag (`CacheKey.FORMAT_TAG`). A later key format gets
another tag, so its keys never collide with these; on Redis and memory,
`clear_pattern("http:v2|*")` removes every HTTP cache entry of this format.

This ensures that:

- The method is part of the key
- Different hosts don't share cache (useful for multi-tenant scenarios)
- Different query parameters get separate cache entries
- The same endpoint with different parameters can be cached independently

Query parameters are ordered by name before the key is built, so `?a=1&b=2`
and `?b=2&a=1` share one entry. The sort is stable: repeated values of one name
keep the order the client sent, because a handler reading `tag: list[str]` sees them in that order, so `?tag=b&tag=a` and
`?tag=a&tag=b` stay two entries. Names are compared decoded (`%61` sorts as
`a`, which is how the key already writes it), and each name and value is encoded
exactly as in the unsorted key, so only the order changes: a query already in
order gets the same key sorted or not. What the key already treats
as equal stays equal (`?a` and `?a=`, an empty segment from `&&`), and nothing
else is merged. Sorting is the default since 0.4.0
([#72](https://github.com/allen0099/FastAPI-CacheX/issues/72), see
[Migrating to 0.4.0](MIGRATING_0_4.md#cache-keys)).

A handler whose response depends on the query order as sent, such as a self or
pagination link copied from `request.url`, should turn it off, or the first
caller's order is cached and served to callers who sent another:

```python
@app.get("/search")
@cache(ttl=60, sort_query=False)
async def search(request: Request, q: str, limit: int = 10):
    return {"self": str(request.url), "items": await run_search(q, limit)}
```

Pass `sort_query=False` to `invalidate()` for such a route as well (see
[Invalidating a single cached route](#invalidating-a-single-cached-route)).

`sort_query` applies to the default key builder only. `build_cache_key()` sorts
too, so a custom `key_builder` that calls it sorts without being told; passing
`sort_query` to `@cache` together with a custom `key_builder` raises
`CacheXError` when the decorator is applied. Call
`build_cache_key(request, ..., sort_query=False)` inside the builder to keep the
order as sent.

A query string longer than 200 bytes, as encoded in the key, is stored as
`sha256:` and its 64-digit hex digest instead, so a client cannot make the
query part of the key arbitrarily long. (Memcached still hashes a whole key over
250 bytes, prefix included; a query just under the threshold with a long host
or path can get there.) The digest is taken after sorting, so reordered long
queries still share one entry. The path stays readable, so `clear_path()` still
finds the entry (with `include_params=True`, since the query is not empty), and
the monitoring routes show the digest as `query_params`. A query as sent never
looks like one: the key writes `:` as `%3A`.

The host and path come from the client, so `|` and `%` in them are percent-encoded
(`%7C` and `%25`). A `Host` header or path containing `|` therefore cannot shift
the components and make one request's key equal another's. The query string is
URL-encoded already. `clear_path()` takes the path as your application sees it
(`request.url.path`) and encodes it the same way; `clear_pattern()` matches the
stored key, so write `%7C` there for a `|`. 0.4.0 does not read entries
written by 0.3.x at all; they are cached afresh once (see
[Migrating to 0.4.0](MIGRATING_0_4.md#cache-keys)).

The host is normalised first, so every spelling of one origin shares an entry:
it is lower-cased (hostnames are case-insensitive), and an empty port or the
scheme's default one (`:80` for http, `:443` for https) is dropped.
`Example.com`, `example.com:80` and `example.com` on http are one key,
`example.com`; `example.com:8080` keeps its port, and an IPv6 literal keeps its
brackets (`[::1]:8000`). The scheme is the one the app sees: behind a proxy that
terminates TLS it is `http` unless the proxy's headers are applied (for
example `uvicorn --proxy-headers`), so a `Host: example.com:443` from such a
proxy keeps its port. A request without a `Host` header uses `unknown`.

Otherwise the host is still whatever the client sends. Unless a reverse proxy
or load balancer in front of the app already rejects unknown hosts, add
Starlette's `TrustedHostMiddleware`, so a forged `Host` gets a `400` instead of
filling the cache with entries no one else will request:

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
http:v2|{method}|{host}|{path}|{query_params}|{component}|...
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
`|` cannot shift the components. An empty string is still a component:
`build_cache_key(request, "")` is not the default key.

Because the path stays in its place, `clear_path()` still finds these
keys: without `include_params` it clears every entry for the path with an empty
query string whatever its extra components, and with it every entry for the
path. The monitoring routes show the extra components, decoded, in
`extra_components`. `default_key_builder(request)` is `build_cache_key(request)`.

`CacheKey` is the same key as a value. `CacheKey.from_request(request,
*components)` builds it, `to_str()` gives the string `build_cache_key` returns,
and `CacheKey.parse(key)` decodes a stored key into `method`, `host`, `path`,
`query` and `extra`, or returns `None` for a key that is not an HTTP key
(a `CacheManager` key, say, or one without the `http:v2` tag):

```python
from fastapi_cachex import CacheKey

for key in await backend.get_all_keys():
    parsed = CacheKey.parse(key)
    if parsed is not None and parsed.path.startswith("/reports/"):
        print(parsed.host, parsed.query, parsed.extra)
```

The Redis and Memcached backends also put their own prefix (`fastapi_cachex:` by
default) in front of every key, so other applications can share the server;
`MemoryBackend` has no prefix. `CacheManager` (see
[Application cache](APP_CACHE.md)) uses a separate, simpler `cache:`-prefixed key
namespace instead of this `|`-separated format, since its keys aren't tied to
HTTP requests.

A `key_builder` that returns a key of its own making, not built by
`build_cache_key()` (or `CacheKey`), still caches, invalidates with
`invalidate()` and clears with `clear_pattern()`. But the key has no `http:v2`
tag, so `clear_path()` does not find it and the monitoring routes skip it.

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
`http:v2|GET|example.com|/greeting||tenant-1|accept-language=de` for
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
deprecated session subsystem's default `header_name`), matched in any case, the component
holds the full hex SHA-256 of the value (trimmed and joined as above) instead
of the value:

```
http:v2|GET|example.com|/me||authorization=sha256:3f0a…(64 hex digits)
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
> If callers authenticate with a cookie that no session middleware loads (for
> example a token cookie your own dependency reads), nothing triggers the bypass: a plain `@cache` serves the first
> caller's response to everyone, so use one of the two options above.

```python
from fastapi import Request

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
async def my_dashboard(user: CurrentUser):
    # Sent as `Cache-Control: private, max-age=60` to a request with
    # `Authorization` or a session: only this backend and the user's browser
    # keep a copy.
    return build_dashboard(user)
```

The per-user entry lives only in your backend. A shared cache in front of the
app (CDN, reverse proxy) sees only the URL, so every response to a request with
`Authorization` or a session carries `private`, even with `cache_authorized`.
When identity comes from a cookie of your own instead, nothing marks the
request as credentialed and the decorator's header goes out unchanged: set
`private=True` (option 1), or send `Vary: Cookie` yourself if a shared cache
may store it.

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

The key builder must be a sync function that returns a `str`; it is called
without being awaited. An `async def` function, an object with an
`async def __call__`, or a `functools.partial` of either is rejected with
`CacheXError` when `@cache` is applied, and by `invalidate()` before it touches
the backend. A builder that still returns something other than a `str` (for
example a sync wrapper that returns a coroutine) raises `CacheXError` on the
request. `fail_open` does not cover this: it is a mistake in the route, not a
backend failure. Read anything async (a user from the database, say) in a
dependency or middleware and put it on `request.state` for the builder.

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

    # Clear by pattern: matched against the whole key http:v2|method|host|path|query
    await cache.clear_pattern("http:v2|GET|*|/api/users/*")
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

`invalidate(request, key_builder=None, vary=None, *, sort_query=None)` returns `True` when an entry existed and
was removed, `False` otherwise, including when no backend is configured. An
error from the backend itself is raised to the caller (see
[When the backend fails](#when-the-backend-fails)). The request you hand it must produce the cached route's key:
same method, host, path and query string. If the cached route uses a custom
`key_builder`, `vary` or `sort_query`, pass the same here, or the key will not
match: `invalidate()` cannot read them from the route. With `vary` only the
variant selected by the request's own header values is deleted, and
`clear_path()` removes all of them. By default the request's query is sorted
as `@cache` sorts it, so `?b=2&a=1` deletes the entry stored for `?a=1&b=2`;
for a route with `sort_query=False` pass `sort_query=False` here too.

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
    dependencies=[Depends(verify_admin)],  # required, keyword-only
    include_content_preview=True,  # default False: no body previews
)
```

- `GET {prefix}/cached-hits` — every cached entry split into method, host, path
  and query (`sha256:<hex>` for a query over 200 bytes), with its ETag and
  expiry, plus counts of valid and expired entries
  and the distinct cached paths. It does not count hits.
- `GET {prefix}/cached-records` — every cached record with its size, expiry,
  `media_type` (the stored response's media type, `null` if it had none) and,
  with `include_content_preview=True`, a preview of the first 100 bytes of the
  cached content. By default `content_preview` is `null` and no response body
  leaves the server; keys, sizes and expiry are still reported.
  `content_type` is always `"bytes"` and is kept for compatibility; read
  `media_type` instead.

Both routes list only route entries (keys in the `http:v2|method|host|path|query`
format); `CacheManager`, session, state and lock keys are skipped, and so are
keys from a `key_builder` that does not use `build_cache_key()`.

> [!WARNING]
> **These routes have no authentication of their own.** `include_in_schema=False`
> only hides them from the OpenAPI document; anyone who guesses the path can read
> them. They expose your whole route structure, including query strings, and
> with `include_content_preview=True` the start of every cached response. So
> `dependencies` is required: pass `dependencies=[Depends(your_auth)]`, or mount
> the routes on an internal-only app. For a local or test app that should stay
> open, pass `dependencies=[]` to opt out deliberately.

The runnable example guards them with a token from an environment variable, and
keeps them closed while the variable is unset:

<!-- fmt:off -->
```python
--8<-- "examples/http_cache.py:admin"
```
<!-- fmt:on -->

> [!NOTE]
> On Memcached, which cannot enumerate keys, both routes return nothing.
