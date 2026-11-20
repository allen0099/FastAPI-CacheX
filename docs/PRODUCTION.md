# Production checklist

What to check before a FastAPI-CacheX app takes real traffic. Each item links
to the section that explains it; most are a line of configuration.

## Backend

- [ ] **Set the backend at startup.** Without one, `@cache` falls back to a
  per-process `MemoryBackend` and logs a `WARNING`: with several workers each
  has its own cache, and an invalidation reaches only one of them. Use Redis
  or Memcached for more than one process
  ([Choosing a backend](BACKENDS.md#choosing-a-backend)).
- [ ] **Give every service and environment its own `key_prefix`.** With the
  default, two apps on one server read each other's values, share lock names
  and tags, and one's `clear()` wipes the other's entries
  ([Choosing a backend](BACKENDS.md#choosing-a-backend)).
- [ ] **Make the backend fail fast.** By default a request takes seconds to
  give up on a Redis that is down. Set short timeouts and `retries=0` (or
  `retry=Retry(NoBackoff(), 0)`)
  ([Failing fast when Redis is down](BACKENDS.md#failing-fast-when-redis-is-down));
  on Memcached, `connect_timeout` and `timeout` ([Memcached](BACKENDS.md#memcached)).
- [ ] **Close the backend on shutdown**, in the app's lifespan
  ([Closing a backend](BACKENDS.md#closing-a-backend)).

## Who sees what

- [ ] **Know which routes are per user.** A request with `Authorization`, a
  session, an API key or a cookie the route's dependencies read bypasses the
  backend unless the route is `public=True` or opts in with
  `cache_authorized=True`, and the opt-in only works when the key tells
  callers apart: a `key_builder` that adds the user, or `vary` on the
  credential header
  ([Requests with credentials](HTTP_CACHING.md#requests-with-credentials),
  [Authenticated endpoints](HTTP_CACHING.md#authenticated-endpoints)).
- [ ] **Vary on every header the response depends on**, such as
  `Accept-Language`; without it the first variant is served to everyone
  ([Varying on request headers](HTTP_CACHING.md#varying-on-request-headers)).
- [ ] **Protect the monitoring routes.** `add_routes()` has no
  authentication of its own; pass `dependencies=[Depends(...)]` that checks an
  admin ([Monitoring routes](HTTP_CACHING.md#monitoring-routes)).

## Load and outages

- [ ] **Coalesce expensive routes.** `coalesce=True` runs the handler once per
  key in each worker for concurrent misses, also while the backend is down;
  `coalesce="distributed"` once across workers
  ([Concurrent misses](HTTP_CACHING.md#concurrent-misses)).
- [ ] **Decide what a route does when the handler fails or is slow.**
  `serve_stale` keeps answering from an expired entry
  ([Serving stale responses](HTTP_CACHING.md#serving-stale-responses)).
- [ ] **Choose `fail_open`.** By default a backend error is logged and the
  request runs uncached; `fail_open=False` turns it into an error
  ([When the backend fails](HTTP_CACHING.md#when-the-backend-fails)).

## Writes and deploys

- [ ] **Invalidate after every write**, with `invalidate()`,
  `invalidate_path()` or tags; tags also work on Memcached
  ([Clearing the cache](HTTP_CACHING.md#clearing-the-cache)).
- [ ] **Handle an invalidation that fails during an outage**: it raises after
  your write has committed
  ([When the backend is down during a write](HTTP_CACHING.md#invalidating-during-an-outage)).
- [ ] **Pick each `ttl` for how stale the data may be.** It is the longest
  an invalidation that never ran leaves old data in place (`ttl + stale_ttl`
  with `serve_stale`).
- [ ] **Plan for deploys that change a response's shape**: old entries are
  served until they expire unless the prefix, the key or a tag changes
  ([Choosing a backend](BACKENDS.md#choosing-a-backend)).

## Observing it

- [ ] **Count hits, misses and errors** with `set_event_handler()`
  ([Counting hits and misses](HTTP_CACHING.md#counting-hits-and-misses)).
- [ ] **Keep the library's log warnings**: backend failures are logged at
  `WARNING` on the `fastapi_cachex.*` loggers, at most once a minute per kind
  ([When the backend fails](HTTP_CACHING.md#when-the-backend-fails)).
- [ ] **Test with a fresh cache per test**, and turn the cache off where a
  test needs every request to run the handler ([Testing your app](TESTING.md)).
