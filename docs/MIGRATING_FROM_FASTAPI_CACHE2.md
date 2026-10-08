# Migrating from fastapi-cache2 {#migrating-from-fastapi-cache2}

[fastapi-cache2](https://github.com/long2ice/fastapi-cache) (imported as `fastapi_cache`) is the most installed FastAPI cache; its latest release, 0.2.2, is from July 2024. This page maps its API onto FastAPI-CacheX, lists the behaviour that differs, and names what has no equivalent. It describes fastapi-cache2 0.2.2 and FastAPI-CacheX 0.5.0. If you are still deciding whether to switch, see [When to use it](COMPARISON.md).

FastAPI-CacheX needs Python 3.10 or newer and FastAPI 0.128.2 or newer.

## A route, before and after {#before-and-after}

With fastapi-cache2:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from fastapi_cache.decorator import cache
from redis import asyncio as aioredis


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    redis = aioredis.from_url("redis://localhost")
    FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/items/{item_id}")
@cache(expire=60)
async def read_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}
```

With FastAPI-CacheX (`uv add "fastapi-cachex[redis]"`):

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fastapi_cachex import BackendProxy, cache
from fastapi_cachex.backends import AsyncRedisCacheBackend


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    backend = AsyncRedisCacheBackend(host="localhost", key_prefix="fastapi-cache:")
    BackendProxy.set(backend)
    try:
        yield
    finally:
        BackendProxy.set(None)
        await backend.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/items/{item_id}")
@cache(ttl=60)
async def read_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}
```

The decorator order is the same in both: the route decorator first, `@cache` directly above the function. The full Redis setup is in [Backends](BACKENDS.md#redis).

## API mapping {#api-mapping}

| fastapi-cache2 | FastAPI-CacheX | Notes |
|----------------|----------------|-------|
| `FastAPICache.init(backend, prefix=...)` | `BackendProxy.set(backend)` | The prefix is the backend's `key_prefix` (default `fastapi_cachex:`). Without a backend, `@cache` falls back to a `MemoryBackend` and logs a warning. |
| `InMemoryBackend()` | `MemoryBackend()` | `MemoryBackend(max_entries=...)` caps it with LRU eviction. |
| `RedisBackend(redis)` | `AsyncRedisCacheBackend(host=..., port=..., password=..., db=...)` | It builds its own client from these settings rather than taking one. |
| `MemcachedBackend(aiomcache.Client(...))` | `MemcachedBackend(servers=["host:port"])` | Uses `pymemcache` (the `memcached` extra). Memcached cannot enumerate keys, so clearing by path or pattern does nothing there. |
| `DynamoBackend` | — | No DynamoDB backend. |
| `@cache(expire=60)` | `@cache(ttl=60)` | `ttl` also takes a `timedelta`. Without `ttl` nothing is stored. |
| `FastAPICache.init(expire=...)` | — | No global default for `@cache`; give each route its `ttl`. `CacheManager(default_ttl=...)` has one for the application cache. |
| `@cache(namespace="items")` | — | Keys are built from the request (see [Keys](#keys)); clear a group of routes by path or pattern instead. |
| `@cache(key_builder=f)` | `@cache(key_builder=f)` | The function takes only the `Request` and returns a `str`. Build it with `build_cache_key(request, *components)` (see [Adding components to the key](HTTP_CACHING.md#adding-components-to-the-key)). |
| `@cache(coder=...)`, `JsonCoder`, `PickleCoder` | — | The rendered response body is stored as is (see [Storage](#storage)). |
| `FastAPICache.clear(namespace=...)` | `await backend.clear_path(path, include_params=True)` or `clear_pattern(...)` | On the backend from the `CacheBackend` dependency or `BackendProxy.get()`. See [Clearing the cache](HTTP_CACHING.md#clearing-the-cache). |
| `FastAPICache.clear(key=...)` | `await invalidate(request)` | Rebuilds the key of the request and deletes it. |
| `FastAPICache.clear()` | `await backend.clear()` | On Memcached this flushes the whole server. |
| `X-FastAPI-Cache: HIT` / `MISS` (`cache_status_header=`) | `@cache(debug_header=True)` sends `X-Cache: HIT`, `MISS` or `BYPASS` | Off by default. |
| `@cache` on a function that is not an endpoint | `@cached(ttl=60)`, or `CacheManager.get_or_set()` | Keyed on the arguments, like fastapi-cache2. See [Caching a function](APP_CACHE.md#caching-a-function). |
| `FastAPICache.init(enable=False)` | — | There is no switch that turns caching off. In tests, set a fresh `MemoryBackend` for each test. |

## Behaviour that differs {#behaviour-that-differs}

### Keys come from the request, not the arguments {#keys}

fastapi-cache2 hashes the function's module, name and arguments. FastAPI-CacheX keys on the request: `http:v2|method|host|path|query`, with the query parameters sorted. In practice:

- Two hosts serving the same app get separate entries, and so do two query strings that FastAPI parses into the same arguments (`?page=1` and `?page=01`).
- A response that depends on a request header needs that header in the key: `@cache(vary=["Accept-Language"])`, or a `key_builder`. fastapi-cache2 would have needed a custom key builder too, unless the header was a function argument.
- Keys from fastapi-cache2 are not read. The first request after the switch is a miss; the old keys expire on their own TTL, or clear them under the old prefix.

### Requests with credentials bypass the cache {#credentials}

fastapi-cache2 caches a request with an `Authorization` header like any other, so a per-user endpoint has to put the user into its key. FastAPI-CacheX does not read or write the shared backend for a request with `Authorization` or a non-empty `request.session`, and answers it with `Cache-Control: private`. If you relied on fastapi-cache2 caching such routes:

- When the response is the same for every user, set `@cache(ttl=60, public=True)`.
- When it is per user, set `cache_authorized=True` with a `key_builder` that puts the verified user into the key. See [Requests with credentials](HTTP_CACHING.md#requests-with-credentials).

A route that sets a cookie is never stored either.

### The client's `Cache-Control` is ignored {#request-cache-control}

fastapi-cache2 skips the cache for a request with `Cache-Control: no-store` and re-renders for `no-cache`. FastAPI-CacheX ignores the request's `Cache-Control`, so no client can send every request to your handler. A matching `If-None-Match` still gets a `304`.

### Storage {#storage}

fastapi-cache2 stores the return value with a coder (JSON by default, pickle optional) and decodes it back into the endpoint's return annotation. FastAPI-CacheX stores the response FastAPI rendered: its body bytes, status code, media type and headers, in a JSON envelope. Nothing is pickled, so an entry read from a shared Redis cannot run code, and any response class can be cached, including `HTMLResponse` and `PlainTextResponse`. A `StreamingResponse` or `FileResponse` is served but not stored.

The application cache (`CacheManager`, `@cached`) stores JSON values; see [JSON round-trip](APP_CACHE.md#json-round-trip) for what comes back.

### Headers {#headers}

Both libraries send `Cache-Control: max-age` and a weak `ETag` and answer a matching `If-None-Match` with `304`. FastAPI-CacheX also writes the other directives from the decorator's arguments (`no_cache`, `no_store`, `private`, `public`, `immutable`, `must_revalidate`, `stale`), sends `Age` on a hit, and answers `HEAD` from the cached `GET`. The handler does not need a `Response` parameter for any of this. See [Cache-Control directives](HTTP_CACHING.md#cache-control-directives).

### Only GET is stored {#methods}

Both libraries cache only `GET`. On a route that also accepts `HEAD`, FastAPI-CacheX answers `HEAD` from the `GET` entry.

## What has no equivalent {#no-equivalent}

- A DynamoDB backend.
- `namespace=`, and clearing by namespace. Clear by path, by pattern, or with `invalidate()`.
- Coders, and decoding the cached value back into the return annotation.
- A global default `expire`, and `enable=False`.
- Python 3.8 and 3.9, and FastAPI releases older than 0.128.2.
