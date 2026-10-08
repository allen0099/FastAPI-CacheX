# When to use FastAPI-CacheX

FastAPI-CacheX caches HTTP responses inside a FastAPI app and gets the HTTP
semantics around them right: `Cache-Control`, `ETag` and `If-None-Match`,
`Vary`, and requests that carry credentials. It also has a small application
cache and a distributed lock on the same backends. It is not the right tool for
every caching job. This page says what it does well, what the other common
choices do well, and when one of them fits better.

The other libraries are described as of their latest release in October 2026.
If something here is out of date, please
[open an issue](https://github.com/allen0099/FastAPI-CacheX/issues).

## At a glance

Facts about the other libraries are from their source code at the release
named in the last row. A feature marked 0.4.2 is not in a FastAPI-CacheX
release yet.

| | FastAPI-CacheX | fastapi-cache2 | cashews | aiocache |
|---|---|---|---|---|
| What it caches | FastAPI GET responses; JSON values; function results (`@cached`, 0.4.2) | Return values of endpoints and plain functions | Return values of any async function | Return values of any async function; a key-value API |
| Key built from | Method, host, path and query of the request | Function module, name and arguments | Function name and arguments, or a template | Function name and arguments, or a key builder |
| `Cache-Control`, `ETag`, `304` | Written from the decorator's arguments; `304` on a matching `If-None-Match` | `max-age`, a weak `ETag` and `304` when the endpoint takes a `Response` | With its `CacheRequestControlMiddleware` and `CacheEtagMiddleware` | None |
| A client's `Cache-Control` request header | Ignored, so clients cannot skip the cache | `no-store` and `no-cache` are honoured | Honoured by the middleware (`no-cache`, `no-store`, `max-age`) | — |
| Requests with `Authorization` or a session | Bypass the shared cache unless you opt in | Cached like any other request | Your choice of key and middleware settings | — |
| Serialization | JSON, no pickle | JSON by default, pickle optional | Pickle by default, optionally HMAC-signed; JSON optional | Pickle, JSON, msgpack, string or none |
| Backends | Memory, Redis, Memcached | Memory, Redis, Memcached, DynamoDB | Memory, Redis (incl. cluster and client-side caching), diskcache | Memory, Redis, Memcached |
| Stampede protection | `get_or_set()` with a distributed lock; `coalesce=True` per process on `@cache` (0.4.2) | None | `locked`, `early`, `soft`, `thunder_protection` | `cached_stampede` (lock based) |
| Tags, early refresh, metrics | No | No | Tags, early and soft refresh, Prometheus middleware, callbacks | Hit/miss and timing plugins |
| Also included | `CacheLock`, atomic counters, monitoring routes | — | Rate limiting, circuit breaker, Bloom filters, locks | `RedLock`, `OptimisticLock`, `multi_cached` |
| Latest release | 0.4.1 (2026-10-03); Python 3.10+, FastAPI 0.133+, Starlette 1.0+ | 0.2.2 (2024-07-24); Python 3.8+ | 7.6.0 (2026-09-17); Python 3.10+ | 0.12.3 (2024-09-25) |

## FastAPI-CacheX

Choose it when the thing you cache is a **FastAPI response** and you want the
cache to behave like an HTTP cache:

- **HTTP semantics.** `@cache` writes `Cache-Control` from its arguments,
  adds a weak `ETag` and answers a matching `If-None-Match` with `304`,
  also on a miss and on `no_cache` routes. It sends `Age` on a hit, honours
  `Vary` (`vary=` adds request headers to the key), and answers `HEAD` from
  the cached `GET` (0.4.2). See [HTTP caching](HTTP_CACHING.md).
- **Safe by default with credentials.** The default key carries no user
  identity, so a request with `Authorization` or a session bypasses the shared
  cache and is answered with `Cache-Control: private`, and a response that sets
  a cookie is never stored. You opt in to sharing with `public=True`, or to a
  per-user entry with `cache_authorized=True` and a key builder that names
  the user (see [Requests with credentials](HTTP_CACHING.md#requests-with-credentials)).
- **Keys from the request.** The key is the method, host, path and sorted
  query string, so it matches what an HTTP cache downstream sees, and a route
  can be dropped with `invalidate(request)` or `clear_path()`.
- **No pickle.** Responses and `CacheManager` values are stored as JSON, so
  an entry read from a shared Redis cannot run code when it is decoded.
- **Fails open.** If the backend is down, `@cache` logs it and runs the
  handler, so a cache outage does not become an API outage.
- **Small extras on the same backend.** `CacheManager` / `AppCache` with
  `get_or_set()` behind a distributed lock, `@cached` for plain functions
  (0.4.2), and `CacheLock` for one holder across workers.

What it does not do, or does only simply:

- Only `GET` (and `HEAD`) responses are cached; there is no caching of `POST`.
- Function caching is basic: no early or probabilistic refresh, no tags, no
  serving of stale values while one caller refreshes, no metrics hooks.
- Three backends: memory, Redis and Memcached. Memcached cannot enumerate
  keys, so pattern and path clearing do nothing there.
- The server-side cache never serves stale content; `stale-while-revalidate`
  and `stale-if-error` are only written into the header for downstream caches.

## fastapi-cache2

[fastapi-cache2](https://github.com/long2ice/fastapi-cache) is the most
installed FastAPI cache. `@cache(expire=60)` caches the return value of an
endpoint or any other function, keyed on the function's module, name and
arguments.

Choose it when:

- You already use it and it works for you. Its API is small and well known.
- You want one decorator for endpoints and helper functions alike, keyed on
  their arguments.
- You need DynamoDB, or Python 3.8 or 3.9.

Points to know when comparing:

- The key does not include the URL. Two requests that reach the same
  arguments share an entry, and a response that depends on something outside
  the arguments, such as a header the handler reads through `Request`, needs a
  custom `key_builder`.
- Requests with `Authorization` or a cookie are cached like any other. A
  per-user endpoint must put the user into the key itself.
- Its latest release is from July 2024.

To switch, see [Migrating from fastapi-cache2](MIGRATING_FROM_FASTAPI_CACHE2.md).

## cashews

[cashews](https://github.com/Krukov/cashews) is a general caching toolkit for
async Python, with the widest feature set of the libraries here.

Choose it when you cache **function results** more than HTTP responses and
need more than a TTL:

- Early or soft refresh, so a hot key is recomputed before it expires instead
  of every caller waiting on a miss.
- Tags, to drop every entry that depends on one record.
- Rate limiting, a circuit breaker, Bloom filters and locks from the same
  setup.
- Prometheus metrics, Redis Cluster, Redis client-side caching or a disk
  cache.

Its FastAPI middlewares add `Cache-Control`, `Age` and `ETag` handling. Unlike
`@cache` here, they let a client's `Cache-Control: no-cache` or `max-age=0`
skip the cache, which is useful for debugging and lets any client send
requests straight to the handler. Values are pickled by default; set a
`secret` so they are signed, or choose the JSON pickler, if anyone else can
write to your Redis.

## aiocache

[aiocache](https://github.com/aio-libs/aiocache) is a general async key-value
cache with decorators (`@cached`, `@cached_stampede`, `@multi_cached`),
pluggable serializers and plugins for hit/miss ratios and timing. It has no
HTTP or FastAPI features of its own.

Choose it when you want a plain async cache API, outside FastAPI or alongside
any web framework, and will handle HTTP headers yourself. Its latest release is
from September 2024.

## An HTTP cache or CDN in front of the app

A reverse proxy (nginx, Varnish) or a CDN (Cloudflare, Fastly, CloudFront)
caches responses before they reach Python at all. For **public responses that
are the same for every visitor**, such as a home page, an article or a public
product list, this is usually the better choice: a hit costs no worker time,
the cache sits close to the client, and it absorbs traffic spikes your app
servers would otherwise take.

It fits less well when:

- The response depends on who is asking. A shared cache keys on the URL (plus
  the headers named in `Vary`), so keeping per-user responses apart there is
  easy to get wrong, and a shared cache does not store a response to a request
  with `Authorization` unless the response explicitly allows it.
- You need to drop an entry from application code the moment the data
  changes, without a call to the CDN's purge API.
- You do not run a proxy or CDN, for example for an internal API.

The two combine. `@cache` writes the `Cache-Control` and `ETag` headers a CDN
reads, so the CDN can serve public routes while `@cache` keeps a server-side
copy for the requests that still reach the app (a cold edge, or a
`no-cache` revalidation). Be careful with `public=True`: it tells every cache downstream that it may
store the response even though the request carried credentials.

## Which one to choose

| You want to… | A good fit |
|--------------|------------|
| Cache FastAPI GET responses with correct `Cache-Control`, `ETag` and `304`s, and keep per-user responses out of the shared cache | FastAPI-CacheX |
| Serve public pages to many visitors at the lowest cost | A CDN or reverse proxy, with `@cache` writing its headers |
| Cache function results with early refresh, tags, rate limiting or metrics | cashews |
| A framework-independent async key-value cache | aiocache, or cashews |
| Keep an existing fastapi-cache2 setup that works for you | fastapi-cache2 |
