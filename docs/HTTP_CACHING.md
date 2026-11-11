# HTTP Caching

The `@cache` decorator caches the responses of FastAPI GET routes (and answers
HEAD from them) and handles `Cache-Control`, `ETag` and `If-None-Match` for
you. This page covers how to use it; [Cache flow](CACHE_FLOW.md) explains what
happens inside a request.

Complete runnable example: [`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py).

## The `@cache` decorator

The routes of [`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py):

<!-- fmt:off -->
```python
--8<-- "examples/http_cache.py:routes"
```
<!-- fmt:on -->

`ttl=60` serves a stored response for 60 seconds, `no_cache=True` makes clients
revalidate every time, and `private=True` keeps a response out of the shared
backend. `no_store=True` keeps it out of every cache; all options are listed
under [Cache-Control directives](#cache-control-directives).

Only GET requests are cached, and HEAD is answered from the GET entry (see
[HEAD requests](#head-requests)); other methods run the handler as usual. The
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
| `public`                 | `public=True`                            | :white_check_mark: | Requests with `Authorization` or session data still use the backend (otherwise they bypass it).               |
| `immutable`              | `immutable=True`                         | :white_check_mark: | None (header only).                                                                                            |
| `must-revalidate`        | `must_revalidate=True`                   | :white_check_mark: | None (header only).                                                                                            |
| `stale-while-revalidate` | `stale="revalidate", stale_ttl=N`        | :white_check_mark: | Header only by default; with `serve_stale=True` an expired entry is served while the handler runs afterwards. See [Serving stale responses](#serving-stale-responses). |
| `stale-if-error`         | `stale="error", stale_ttl=N`             | :white_check_mark: | Header only by default; with `serve_stale=True` a failing handler is answered from the expired entry. See [Serving stale responses](#serving-stale-responses). |
| `s-maxage`               | `s_maxage=N`                             | :white_check_mark: | None (header only): the stored response still expires after `ttl`.                                             |
| `proxy-revalidate`       | `proxy_revalidate=True`                  | :white_check_mark: | None (header only).                                                                                            |
| `no-transform`           | `no_transform=True`                      | :white_check_mark: | None (header only).                                                                                            |
| `must-understand`        | —                                        | :x:                | —                                                                                                              |

`s-maxage` and `proxy-revalidate` speak to shared caches (a CDN or reverse
proxy), so `s_maxage` lets one keep a response for a different time than
browsers do, with or without a `ttl`: `@cache(ttl=60, s_maxage=300)` sends
`max-age=60, s-maxage=300`. Neither is sent on a response meant for one caller
(the `private` header a response gets when it sets a cookie or answers a
request with credentials), and combining either with `private=True` is
rejected when the decorator is applied. `no-transform` is sent in every case.
`must-understand` is not produced: it belongs with `no-store`, which
`no_store=True` sends alone. A shared cache that honours `s-maxage` treats the
response as `proxy-revalidate` too (RFC 9111 §5.2.2.10), so it does not serve
it stale: `stale=...` then only reaches browsers.

`no_cache=True` and `no_store=True` replace the rest of the header: with
`no_cache` only `no-cache` (and `must-revalidate`, `proxy-revalidate` and
`no-transform`, when set) is sent, and with `no_store` only `no-store`. How the other arguments combine is described in
[Cache flow](CACHE_FLOW.md#2-cache-control-directives).

`no_store=True` together with any other caching argument (`ttl`, `stale`,
`no_cache`, `public`, `private`, `immutable`, `must_revalidate`, `s_maxage`,
`proxy_revalidate`, `no_transform`) emits a
`UserWarning` pointing at your `@cache(...)` line when the decorator is
applied: `no_store` overrides them, and the warning names them.

A bare `@cache()`, with neither `ttl` nor a directive, stores nothing and has no
`Cache-Control` of its own. It adds an ETag and answers a matching
`If-None-Match` with `304`, and keeps the handler's own `Cache-Control` (or
sends none). As on every route, a response that sets a cookie or answers a
request with `Authorization` or session data is still sent with `private`.

### The request's `Cache-Control` is ignored

A client's own `Cache-Control` request header (`no-cache`, `max-age=0`, as sent
by a browser's hard reload, and so on) does not change what `@cache` does. This
is deliberate: if a request header could bypass the cache, any client could
send every request straight to your handler. Conditional requests are honoured:
a matching `If-None-Match` gets a 304.

A route can opt in with `client_cache_control`, for an internal API or for
callers you trust:

```python
def is_staff(request: Request) -> bool:
    # request.state.user: set by your own authentication middleware
    return request.state.user.is_staff


@app.get("/report")
@cache(ttl=300, client_cache_control=is_staff)  # or True, for every client
async def report() -> dict: ...
```

| Request `Cache-Control` | What the route then does |
|---|---|
| `no-cache` or `max-age=0` | Runs the handler and stores the new response in place of the entry, so later requests get it too. |
| `no-store` | Runs the handler without reading or writing the backend; the entry stays as it is. |
| Anything else | Ignored, as before. |

The callable takes the request, returns whether this caller may, and is only
called for a request that carries one of these directives. It must be a plain
function, not `async` (the decorator rejects one), and only `True` itself opens
the route. `Pragma: no-cache` is not read. A route that never reads the backend (`private=True`,
no positive `ttl`, or a request with credentials that bypasses it) is not
affected. With `client_cache_control=True` any client can run the handler on
every request, which is what the default prevents; keep it for routes where
that is acceptable.

## Cache hit behavior

When a cached entry is valid (within TTL):

- **Default behavior**: Returns the cached content directly, with the status code and headers the handler originally produced (every line of a header sent more than once), without re-executing the endpoint handler
- **With `If-None-Match` header**: Returns HTTP 304 Not Modified if the ETag matches
- **With `no-cache` directive**: Forces revalidation with fresh content before deciding on 304
- **With `private=True`**: Nothing is read from or written to the shared backend; the handler runs every time and only `If-None-Match` revalidation applies
- **Without `ttl`** (`ttl=None`): Nothing is read from or written to the backend, as with `private=True`. The handler runs on every request, and `If-None-Match` gets a 304 only when it matches the freshly rendered response, so an old ETag never gets a 304 once the content has changed
- **With `ttl=0`**: Sends `max-age=0` and otherwise behaves like `ttl=None`. A negative `ttl`, a non-`int` one (such as `1.5` or `True`) and one above `MAX_TTL` (see [TTL values](BACKENDS.md#ttl-values)) are rejected with `TTLTypeError` or `TTLValueError` when the decorator is applied; both are `CacheXError` subclasses, the same ones the backends raise (see [TTL values](BACKENDS.md#ttl-values))
- **With a `timedelta`**: `ttl=timedelta(minutes=5)` is the same as `ttl=300`, and so is `stale_ttl`; a `timedelta` with a fraction of a second is rejected like a `float`

### What still runs on a hit

"Without running the handler" covers the route function only:

- **Dependencies run on every request, hit or miss.** FastAPI resolves them
  before it calls the route function, and `@cache` wraps only that function.
  A database session opened in a dependency is opened on a hit too, and a
  dependency that counts or rate-limits requests counts the hit. Per-user key
  builders rely on this: they read what a dependency put into `request.state`.
  The headers and cookies a dependency sets are sent with the hit as well
  (see [Requests with credentials](#requests-with-credentials) for how they
  affect storage).
- **Background tasks added by the handler run only on a miss.** On a hit the
  route function never runs, so nothing is added to its `BackgroundTasks`, and
  a `Response.background` the handler returned is not stored with the entry.
  A task that a dependency adds through its own `BackgroundTasks` parameter
  runs on a hit too, since the dependency ran.
- **Middleware** runs as for any request, around the whole thing.

So put work that must happen on every request (auditing, rate limiting,
authentication) in a dependency or middleware, and work that belongs to
producing the response (sending the e-mail about it) in the handler.

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

- **The request carries `Authorization` or session data.** As RFC 9111 §3.5 requires of a
  shared cache, the backend is bypassed, as with `private=True`: nothing is
  read or written, the handler runs, and `If-None-Match` is compared against
  the fresh render. The response (and a 304) is sent with `private` in place
  of `public`, keeping the decorator's other directives except `s-maxage` and
  `proxy-revalidate` (`private, no-cache`
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
  A request has session data when `request.session` is non-empty under any
  session middleware, such as Starlette's `SessionMiddleware`. An empty
  session does not count, and neither does a `Cookie` header on its own.
  Before 0.3.9 only `Authorization` did, and a plain `@cache` on a route
  that read the session served one visitor's response to the next (#319).
  Before 0.5.0 a session that the removed `FastAPICacheXSessionMiddleware`
  loaded (from `X-Session-Token`, a bearer token or its cookie) counted too;
  see [Migrating to 0.5.0](MIGRATING_0_5.md#cache-session-token).
  The first bypass on each route that reads the backend is logged at `WARNING` (see
  [Requests with credentials](#requests-with-credentials)).
- **The handler's own `Cache-Control` contains `private` or `no-store`**
  (as whole directives, in any case). The response is served but not stored,
  and the handler's header is sent unchanged instead of the decorator's.
- **The response sets a cookie.** It is served, `Set-Cookie` included, but not
  stored, and it (and a 304) is sent with `private` in place of `public`,
  keeping the other directives except `s-maxage` and `proxy-revalidate`, so a
  shared cache downstream does not store it
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

### HEAD requests

A route that accepts HEAD gets the same treatment as GET (#253). `@app.get`
registers GET only, so list both methods:

```python
@app.api_route("/items", methods=["GET", "HEAD"])
@cache(ttl=60)
async def list_items() -> list[str]: ...
```

A HEAD request reads the entry a GET stored under the same key, as RFC 9110
§9.3.2 allows: on a hit it gets the stored status and headers, `ETag`, `Age`
and the `Content-Length` of the stored body, without running the handler, and a
matching `If-None-Match` gets a 304. The key is the one a GET would get: a
custom `key_builder` is called with the request's method set to `GET`. `vary`,
credentials, `private`, `no_store` and dependency headers apply as for GET.

On a miss the handler runs and its response gets the same `Cache-Control` and
`Vary` handling as a GET, with an `ETag` and `Content-Length` computed from what
it rendered: a handler that skips the body for HEAD sends those of the empty
body. That response is not stored, since a later GET would then be served the
empty body. The server drops the body of every HEAD response.
`invalidate()` builds the GET key, so it clears what HEAD reads as well.

A route that already accepted HEAD ran the handler on every HEAD request before
0.5.0 and added none of these headers; it now skips the handler on a hit.

### Concurrent misses

By default every request that misses runs the handler: 20 concurrent requests
to a cold key, or arriving just after its entry expired, run it 20 times.
`coalesce=True` runs it once per key in each process:

```python
@app.get("/report")
@cache(ttl=60, coalesce=True)
async def report() -> dict[str, int]: ...
```

The first request to miss renders as usual. Requests that miss the same key
while it runs wait for it to answer, then read the backend again and are served
the stored entry, with an `ETag`, `Age` and 304 handling like any hit (#252).
When the first response was not stored (it set a cookie, was `private` or
`no-store`, had an error status or a status a dependency set, was streamed, or
the handler raised), each waiting request runs the handler itself, all at once
rather than one after another.

- Only requests in one process are coalesced, without a lock in the backend:
  each worker still runs the handler once per cold key. For one run across
  workers, cache the expensive part with `get_or_set()`, whose
  [stampede protection](APP_CACHE.md#stampede-protection) uses a distributed
  lock.
- A waiting request waits as long as the first one's handler takes; there is
  no separate timeout. A request whose backend read fails does not wait.
- A HEAD request waits for a running GET of the same key but never makes
  others wait, since its response is not stored.
- It needs a positive `ttl`. With `private=True` or `no_cache=True`, which
  never serve a stored entry, the decorator raises `CacheXError`; with
  `no_store=True` it is ignored with the usual warning. Requests with
  credentials that bypass the backend are not coalesced.

### Serving stale responses

By default `stale=` and `stale_ttl=` only write `stale-while-revalidate` or
`stale-if-error` into `Cache-Control`, for the caches downstream. With
`serve_stale=True` this server's own cache acts on them too:

```python
@app.get("/report")
@cache(ttl=60, stale="revalidate", stale_ttl=300, serve_stale=True)
async def report() -> dict: ...
```

The entry is then kept for `ttl + stale_ttl` (360 seconds here). For the first
`ttl` seconds it is a normal hit. After that, for `stale_ttl` seconds:

| `stale=` | What a request gets once the entry is past `ttl` |
|---|---|
| `"revalidate"` | The stored response, at once. The handler runs after that response was sent and its result replaces the entry, so nobody waits for the handler. |
| `"error"` | The handler runs. If it raises or answers with a 5xx status, the stored response is sent instead; otherwise the new response is stored and sent as usual. |

A stale response carries an `Age` above `ttl`, and `X-Cache: STALE` with
`debug_header=True`. Past `ttl + stale_ttl` the entry is gone and the request
is an ordinary miss.

With `stale="revalidate"`:

- The refresh is a background task of the request that found the entry stale.
  The dependencies of that request are still open while the handler runs, the
  request's other background tasks run as usual, and a handler that takes
  `BackgroundTasks` gets the same object. A dependency declared with
  `Depends(..., scope="function")` has already been closed by then; a handler
  that needs one should not use `stale="revalidate"` with `serve_stale`.
- One refresh per key runs at a time in each process. With several workers,
  each may run one.
- A `HEAD` request is answered from the stale entry and starts no refresh,
  since its response is never stored.
- If the refresh fails (the handler raises, or its response is one that is not
  stored), a warning is logged, the stale entry stays, and the next request
  tries again.

With `stale="error"`, an `HTTPException` below 500 is the handler's answer and
is sent as it is: a 404 is not a failure. The same goes for an exception the
application has registered a handler for with `add_exception_handler`, which
turns it into a response of its own. Without a stale entry (a cold key, or one
past the window) the error is sent as it is. Each fallback logs one warning
line. With `coalesce=True`, requests that find the entry stale at the same
time run the handler once, as for a miss.

`serve_stale=True` needs `stale`, `stale_ttl` and a positive `ttl`, and is
rejected together with `private=True`, `no_cache=True` or
`must_revalidate=True`, which never allow a stale response. Whether an entry
is past its `ttl` is read from the time stored with it, on the clock of the
server that answers; servers whose clocks differ by seconds will disagree by
that much about when an entry turns stale.

Two things to know when operating such a route. Entries stay in the backend
for `ttl + stale_ttl`, so after `serve_stale` is turned off again, entries
written before may be served as ordinary hits for up to `stale_ttl` past their
`ttl`; clear the route's entries if that matters. And the
[monitoring routes](#monitoring-routes) report an entry as valid for as long
as the backend keeps it, stale or not.

### Telling a hit from a miss

Nothing in a response says whether the handler ran, short of adding counters
to it. `debug_header=True` sends `X-Cache` on every GET or HEAD response:

```python
@app.get("/items")
@cache(ttl=60, debug_header=True)
async def items() -> list[str]: ...
```

| `X-Cache` | Meaning |
|-----------|---------|
| `HIT`     | Served from the stored entry: a 200 without running the handler, or a 304 for the entry's ETag. |
| `MISS`    | The backend was consulted and the handler ran: a cold or expired key, `no_cache=True`, or a response that was not stored (a cookie, an error status, a streamed body). |
| `BYPASS`  | The backend was neither read nor written: `no_store=True`, `private=True`, no positive `ttl`, a request with credentials on a route without `public` or `cache_authorized`, or a request's `no-store` on a route with `client_cache_control`. |
| `REFRESH` | A request's `no-cache` or `max-age=0` skipped the entry on a route with [`client_cache_control`](#the-requests-cache-control-is-ignored): the handler ran, and its response replaced the entry unless it was one that is not stored (see `MISS`). |
| `STALE`   | An entry past its `ttl` was served on a route with [`serve_stale`](#serving-stale-responses): at once while the handler runs afterwards (`stale="revalidate"`), or because the handler failed (`stale="error"`). |

It is off by default, since it tells clients what the server caches; turn it
on while developing or behind an internal route. The header is never stored
with the entry, and it replaces an `X-Cache` the handler sets itself. Other
methods get no header, as they get no `Cache-Control`.

### Requests with credentials

A single-page app that sends `Authorization` on every request, or a site where
every visitor has session data, gets no cache hits at all on a plain `@cache`
route: each request bypasses the backend (see above). Pick the option that
matches what the handler returns:

- **The response is the same for every user** (a product list, a public
  article): set `public=True`. Requests with `Authorization` or session data then
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
header or non-empty `request.session` data) and the two
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

This only covers `@cache`. `invalidate()`, `CacheManager` and `CacheLock` still
raise backend errors to the caller.

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

The names are also added to the `Vary` header of every response to a GET or HEAD
request on the route, on a 200 or a 304, served from the backend or not
(`private`, `no_store`, a bypassed `Authorization` request or a response that
is not stored), so a shared cache in front of the app keys on them too. A name
the response already lists (in any case) is not repeated, and a response with
`Vary: *` is left alone.

`vary` must be a list (or tuple) of header field names, or a mapping from
names to [normalisers](#vary-normaliser). A bare string such as
`vary="Accept"` is rejected when the decorator is applied, as are empty names,
`*` and anything that is not a valid field name.

#### Normalising a header's value {#vary-normaliser}

Header values come from the client, so every distinct value is its own entry:
`Accept-Language: en-US,en;q=0.9` and `Accept-Language: en` are stored twice
even when the route only serves `en` and `zh-TW`. To key on the values the
route really distinguishes, give `vary` a mapping from the header name to a
function:

```python
def locale(value: str) -> str:
    return "zh-TW" if value.lower().startswith("zh") else "en"


@app.get("/greeting")
@cache(ttl=300, vary={"Accept-Language": locale})
async def greeting(request: Request):
    return {
        "text": translate("hello", locale(request.headers.get("accept-language", "")))
    }
```

- The function gets the header's value as described above (trimmed, repeated
  lines joined, `""` when the header is missing) and returns the `str` or `int`
  that goes into the key: `accept-language=en`. Anything else raises
  `CacheXError`.
- A name mapped to `None` is keyed on its own value, exactly as in a list, so
  `vary={"Accept-Language": locale, "Origin": None}` mixes both.
- The `Vary` response header still names the header. A cache in front of the
  app cannot run your function, so it keeps one copy per raw value; the bound
  applies to this library's backend.
- `invalidate()` and `invalidate_path()` take the same mapping and build the
  same component. Passing the list form for a route that uses a normaliser
  builds another key and deletes nothing.
- The function must be a plain `def` (an `async def` is rejected when the
  decorator is applied). It runs once for every GET or HEAD request that is
  looked up in the cache, before the backend is read, so keep it cheap; it is
  not called for other methods or for a request that bypasses the cache. It is
  your code: an exception it raises fails the request and is not covered by
  `fail_open`.
- To annotate one, import `VaryNormaliser` from `fastapi_cachex`
  (`Callable[[str], str | int]`).
- A credential header's normalised value is still [hashed](#credential-headers-are-hashed),
  and `Cookie` with a normaliser does not emit the warning described there.

#### Credential headers are hashed

The key is not secret: it is listed by `get_all_keys()`, shown by the
`/cached-records` and `/cached-hits` monitoring routes, and stored as-is in the
Redis or Memcached keyspace. So for the headers that carry credentials,
`Authorization`, `Proxy-Authorization`, `Cookie` and `X-Session-Token` (the
token header of the session middleware removed in 0.5.0), matched in any case, the component
holds the full hex SHA-256 of the value (trimmed and joined as above) instead
of the value:

```
http:v2|GET|example.com|/me||authorization=sha256:3f0a…(64 hex digits)
```

The same token always gives the same digest, so it hits its own entry, and two
tokens give two entries. A missing or empty credential header is not hashed:
it stays `authorization=`, like any other empty header, so every anonymous
caller shares one entry and the key still shows that it is the anonymous one.
Every other header, including a custom token header such as `X-API-Key`,
stays readable; if yours carries a secret, key on it through a `key_builder`
(hashing it yourself) rather than `vary`.

`vary=["Authorization"]` does not lift the rule for authorized requests (see
[Authenticated endpoints](#authenticated-endpoints)): a request with an
`Authorization` header (or session data) still bypasses the backend unless the route is
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
`Authorization` or session data triggers the bypass), but a response that sets a cookie is
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
>    header or session data: without it such requests bypass the backend too.
>
> If callers authenticate with a cookie that no session middleware loads into
> `request.session` (for
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
    # `Authorization` or session data: only this backend and the user's browser
    # keep a copy.
    return build_dashboard(user)
```

The per-user entry lives only in your backend. A shared cache in front of the
app (CDN, reverse proxy) sees only the URL, so every response to a request with
`Authorization` or session data carries `private`, even with `cache_authorized`.
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
with `Authorization` or session data on a route without `public=True` or `cache_authorized=True`. Before 0.3.8
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

### By tag

When the same data appears in several routes, tag the routes and invalidate
the tag instead of naming every path:

```python
from fastapi_cachex import cache, invalidate_tags


@app.get("/users")
@cache(ttl=300, tags=["users"])
async def list_users(): ...


@app.get("/users/{user_id}")
@cache(
    ttl=300,
    tags=lambda request: ["users", f"user:{request.path_params['user_id']}"],
)
async def read_user(user_id: int): ...


@app.put("/users/{user_id}")
async def update_user(user_id: int):
    await save_user(user_id)
    await invalidate_tags(f"user:{user_id}")  # that user's entries
    # await invalidate_tags("users") would drop the list and every user
```

`tags=` is a list of names, or a plain function that takes the request and
returns them. An entry is a miss once any of its tags has been invalidated
since it was stored, whatever its query string, `vary` variant or host. It
works on every backend, Memcached included, because nothing is enumerated:
each tag has a version in the backend, and an entry remembers the versions it
was stored under.

- A hit on a tagged route costs one extra batched backend read; a route
  without tags pays nothing.
- Invalidated entries are not deleted; they expire with their `ttl`.
- A tag's version is created when a response is stored under it. A request
  whose response is not stored (a 404, a cookie) leaves nothing behind, so
  tags built from the URL cannot be used to fill the backend.
- The versions are read before the handler runs, so a response built from
  data that changed meanwhile is not served after the invalidation.
- The tags are shared with the application cache: see
  [Invalidating a group of values with tags](APP_CACHE.md#group-invalidation-on-memcached),
  which also covers what happens when a tag's version is lost and during an
  upgrade from a release without tags.

### Invalidating a single cached route

`clear_path`/`clear_pattern` work on ranges of keys. To drop exactly the entry a
`@cache`-decorated route would use — typically right after a mutation — call
`invalidate_path()` from the handler that changed the data. It rebuilds the
key the cached `GET` of a path would use, with the same key builder, and
deletes it:

```python
from fastapi import Request

from fastapi_cachex import cache, invalidate_path


@app.get("/items/{item_id}")
@cache(ttl=300)
async def read_item(item_id: int):
    return await load(item_id)


@app.put("/items/{item_id}")
async def update_item(item_id: int, request: Request):
    await save(item_id)
    return {"invalidated": await invalidate_path(request, f"/items/{item_id}")}
```

`invalidate_path(request, path, key_builder=None, vary=None, *, query=None, sort_query=None)`
takes the host, the scheme and the headers from the current request and the
method (`GET`), path and query from its arguments. `path` is the path the
cached handler sees in `request.url.path`: already percent-decoded, so build
it from the decoded values as above, and with the prefix of a mounted app
included. `request.url_for(...).path` gives the same string except behind a
proxy that strips a prefix (it adds `root_path`, which the key does not hold)
and for a path parameter that contains `?` or `#`. The query of the
cached request goes in `path` (`"/items?page=2"`) or in `query`, as a string, a
mapping or a sequence of pairs; without one, the entry stored for the path
with no query is deleted. Because a `?` in `path` starts the query, a literal
one inside the path is written as `request.url.path` shows it (`%3F` on
current Starlette).

`invalidate(request)` is the same operation for a request you already have:
it builds the key from that request as it is, method included. Passing it the
`PUT` request itself therefore deletes nothing, and logs a warning that says
so, because the key of a `PUT` never matches a cached `GET`.

Both return `True` when an entry existed and
was removed, `False` otherwise, including when no backend is configured. An
error from the backend itself is raised to the caller (see
[When the backend fails](#when-the-backend-fails)). The request you hand `invalidate()` must produce the cached route's key:
same method, host, path and query string. If the cached route uses a custom
`key_builder`, `vary` or `sort_query`, pass the same to either function, or the key will not
match: they cannot read them from the route. With `vary` only the
variant selected by the request's own header values is deleted, and
`clear_path()` removes all of them. By default the request's query is sorted
as `@cache` sorts it, so `?b=2&a=1` deletes the entry stored for `?a=1&b=2`;
for a route with `sort_query=False` pass `sort_query=False` here too.

### After a write: `invalidate()` or `clear_path()`

`invalidate()` and `invalidate_path()` delete exactly one key, the one built from the request or path you
pass. After `PUT /items/42` that is the entry for `GET /items/42` with no
query string and, on a route with `vary`, the one variant the request's
headers select. Entries that show the same item under another key keep
serving the old data until they expire: `/items/42?fields=name`, a list at
`/items?page=2` that includes it, and the `Accept-Language: fr` variant when
you invalidated with `en`.

- `invalidate_path(request, path)` (or `invalidate(request)`) when the resource has one entry, or you want to drop
  just that one. One backend operation, and it works on Memcached.
- `clear_path(path, include_params=True)` when the resource has several
  entries: it removes every entry stored for the path, across query strings,
  `vary` variants, hosts and methods. Call it for the item and for the lists
  that include it. Redis and memory only: Memcached cannot enumerate keys.
- [`invalidate_tags(...)`](#by-tag) when the data appears under several paths,
  or the backend is Memcached: tag the routes, and one call invalidates every
  entry stored under the tag.

```python
from fastapi_cachex import CacheBackend


@app.put("/items/{item_id}")
async def update_item(item_id: int, cache: CacheBackend):
    await save(item_id)
    # Every variant of the item, then every page of the list.
    await cache.clear_path(f"/items/{item_id}", include_params=True)
    await cache.clear_path("/items", include_params=True)
```

## Counting hits and misses

`set_event_handler()` registers one function that is told what the cache
does, for `@cache` routes and for `CacheManager` / `@cached` alike. The
library ships no metrics dependency; the function forwards the events to
whatever you measure with:

```python
from prometheus_client import Counter, Histogram

from fastapi_cachex import CacheEvent, set_event_handler

EVENTS = Counter("cache_events_total", "Cache events", ["source", "kind", "route"])
SECONDS = Histogram("cache_answer_seconds", "Time to answer", ["kind", "route"])


def record(event: CacheEvent) -> None:
    route = event.route or ""
    EVENTS.labels(event.source, event.kind, route).inc()
    if event.duration is not None:
        SECONDS.labels(event.kind, route).observe(event.duration)


set_event_handler(record)
```

| `kind` | When |
|---|---|
| `hit`, `miss`, `stale`, `refresh`, `bypass` | A GET or HEAD request to a `@cache` route was answered: the values of [`X-Cache`](#telling-a-hit-from-a-miss), reported whether or not `debug_header` is set, and also when the handler raised. With `source="app"`, `hit` and `miss` say whether `CacheManager.get()` or `get_or_set()` (and so `@cached`) found the value; a `get_or_set()` that waited for another caller to compute the value is a `hit`. |
| `store` | A response or a value was written. |
| `invalidate` | An entry was deleted by `invalidate()`, `invalidate_path()` or `CacheManager.delete()`. |
| `error` | A backend call failed. For `@cache`: any exception from the backend, whether the request then went on without the cache (`fail_open=True`) or failed with it. For `CacheManager` and `@cached`: only a backend that could not be reached (`BackendUnavailableError`, which is raised as before). |

Each event has a `source` (`"http"` or `"app"`), the `key` (None when the
backend was not consulted, or the call was not about one key), for a request to a `@cache` route the `route` as
its path template (`/api/users/{user_id}`, with the prefix of an included
router or a mounted application), and on the events that answer a request the
`duration` in seconds. Use `route` as a metric label, not `key`: a key holds
the query string, other client input and whatever a custom key builder adds,
and has no bound. For the same reason, do not log it as it is.

The handler runs synchronously on the request's path, so keep it to
incrementing a counter. It must be a plain function: an `async` one is
rejected with `TypeError`. An exception it raises is logged and does not reach
the request. `set_event_handler(None)` removes it; while none is set, no event
is built. `add()`, `increment()`, `has()`, `get_many()`, `set_many()` and the
`clear*` methods report nothing but such an `error`.

On the application side an `error` means the server could not be reached: a
refused or reset connection, a timeout. An error the server answers with (a
read-only replica, out of memory, a value too large) raises as before and is
not reported, and a backend of your own is reported only when it raises
`BackendUnavailableError`. To alert on every failure of the application cache,
watch the exceptions as well.

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
  expiry, in `entries`, counted in `total_entries`, `valid_entries` and
  `expired_entries`, plus the distinct cached paths. Despite the route's name,
  it does not count hits. The older `cached_hits`, `total_hits`, `valid_hits`
  and `expired_hits` fields were [removed in 0.6.0](MIGRATING_0_6.md#cached-hits-fields).
- `GET {prefix}/cached-records` — every cached record with its size, expiry,
  `media_type` (the stored response's media type, `null` if it had none) and,
  with `include_content_preview=True`, a preview of the first 100 bytes of the
  cached content. By default `content_preview` is `null` and no response body
  leaves the server; keys, sizes and expiry are still reported.
  `content_type` is always `"bytes"` and is kept for compatibility; read
  `media_type` instead.

Both routes list only route entries (keys in the `http:v2|method|host|path|query`
format); `CacheManager` and lock keys are skipped, and so are
keys from a `key_builder` that does not use `build_cache_key()`.

> [!WARNING]
> **These routes have no authentication of their own.** `include_in_schema=False`
> only hides them from the OpenAPI document; anyone who guesses the path can read
> them. They expose your whole route structure, including query strings, and
> with `include_content_preview=True` the start of every cached response. So
> `dependencies` is required: pass `dependencies=[Depends(your_auth)]`, or mount
> the routes on an internal-only app. For a local or test app that should stay
> open, pass `dependencies=[]` to opt out deliberately.

[`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py) guards them with a token from an environment variable,
and keeps them closed while the variable is unset:

<!-- fmt:off -->
```python
--8<-- "examples/http_cache.py:admin"
```
<!-- fmt:on -->

> [!NOTE]
> On Memcached, which cannot enumerate keys, both routes return nothing.
