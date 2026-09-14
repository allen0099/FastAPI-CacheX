# Known Limitations and Planned Work

Things that are understood, deliberately not done yet, and worth knowing about
before you hit them. Shipped changes live in [CHANGELOG.md](../CHANGELOG.md).

## Known limitations

### Cache keys do not normalize query parameter order

`default_key_builder` uses `str(request.query_params)` as-is, so `?a=1&b=2` and
`?b=2&a=1` are two separate entries for the same logical resource — duplicated
storage and a duplicated miss. Sorting them would invalidate every existing
cache key, so it is a 0.4.0 change.

### `trusted_proxies` matches exact strings, not CIDR ranges

`SessionConfig.trusted_proxies` compares the peer address literally. Cloud
load balancers usually come from a range (`10.0.0.0/8`), which cannot be
expressed today. The default is "trust nothing", so this is a usability gap
rather than a security hole: an unlisted proxy means forwarded headers are
ignored, not blindly trusted.

Parsing values with `ipaddress.ip_network` would fix it, keeping plain-string
comparison for non-IP peer names such as `testclient`.

### The Redis backend reports no TTL to the monitoring routes

`AsyncRedisCacheBackend.get_cache_data()` returns `(entry, None)` for every key,
so `/cached-records` shows Redis entries as never expiring. Expiry still happens
— it is the display that is wrong. Fixing it means pipelining a `PTTL` per key
alongside the existing fetch, which must stay a single round-trip.

### Cookies cannot be named in `token_source_priority`

`token_source_priority` accepts only `"header"` and `"bearer"`. Cookie transport
is read solely by `FastAPICacheXSessionMiddleware`, and the response side is a
binary decision driven by the token's source; folding cookies into the same
priority list would let `["cookie"]` fail silently on the deprecated
`SessionMiddleware`. Once 0.3.5 removes that class, one priority list can
describe all three sources. See [SESSION.md](SESSION.md).

### `get_app_cache` can race on the very first request

`get_app_cache` is a sync dependency, so FastAPI runs it in a threadpool. Two
concurrent first requests can each build a `MemoryBackend`, with the second
`BackendProxy.set()` overwriting the first — briefly two caches that cannot see
each other. It only affects applications that never configure a backend at all;
setting one in the lifespan avoids it entirely. A fix (locking the lazy
initialization, or an async dependency) is an API change.

## Planned work

### Vary-aware cache keys

A way to fold selected request headers into the cache key —
`@cache(vary=["accept-language"])` or a `vary_on_headers(...)` key-builder
factory — and to emit the matching `Vary` header. This is the general form of
per-user caching; `private=True` currently only opts out of the shared backend,
it cannot cache per user.

### Cache-Control directives that are defined but never emitted

`DirectiveType` declares `s-maxage`, `no-transform`, `proxy-revalidate` and
`must-understand`, but `cache()` never produces them (the README support table
marks them unsupported). Wiring them up means new decorator parameters and
handling the combinations that conflict.

### `add_routes` content previews

`/cached-records` includes the first 100 characters of every cached response.
The endpoints already carry a warning that they have no authentication, but an
`include_content_preview` switch would let deployments keep the route while
dropping the payload.

### 0.4.0: `delete()` returning `bool`

`BaseCacheBackend.delete()` returns `None` for 0.3.x compatibility; atomicity is
available through `get_and_delete()` instead. Returning whether anything was
deleted is the better signature, and 0.4.0 is when it can change. The same
release should drop `BackendProxy.get_backend()`/`set_backend()`, already marked
for removal in 0.4.0.
