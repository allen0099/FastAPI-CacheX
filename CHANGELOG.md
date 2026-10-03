# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file records what changed for users of the library, in particular
behaviour that changed under an unchanged API. The pull request that earns an
entry adds it as a fragment in `changelog.d/`, and the release merges the
fragments in here, so `## [Unreleased]` is usually empty between releases. Each
entry opens with a bold one-line summary: `- **What changed.** The details...`.
The GitHub release notes list those summaries and link back here, and a release
fails if there is nothing to release or an entry has no summary. Entries before
0.3.8 predate this format. See
[Changelog fragments](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#changelog-fragments)
and [Releasing](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#releasing).

Note that 0.3.3 was never released; 0.3.4 follows 0.3.2.

## [Unreleased]

## [0.4.1] - 2026-10-03

### Changed

- **A dependency that sets a cookie keeps `@cache` routes out of the backend.**
  Before 0.4.1, a handler that did not declare `response: Response` dropped the
  cookie and the response was stored; now the cookie is sent and the response is
  `private` and not stored. An app-wide dependency that sets a cookie on every
  request therefore turns storage off for every `@cache` route it applies to, and
  so does one that sets the status code. `@cache` also adds the dependencies'
  headers to a `Response` the handler returns itself, which FastAPI does not do.
  Entries stored before the upgrade may still replay dependency headers until
  their TTL ends. ([#233](https://github.com/allen0099/FastAPI-CacheX/issues/233))
- **`@cache(no_store=True)` warns about the arguments it overrides.** A
  `UserWarning` naming them is emitted when the decorator is applied with
  `no_store=True` and `ttl`, `stale`, `no_cache`, `public`, `private`,
  `immutable` or `must_revalidate`. The response is unchanged: `no-store` only. ([#328](https://github.com/allen0099/FastAPI-CacheX/issues/328))

### Fixed

- **Headers and cookies a dependency sets on the shared `Response` reach the
  client on every `@cache` response, with this request's values.** A handler that
  did not declare `response: Response` lost them, and one that did stored the
  dependency's headers with the entry and replayed the values of the request that
  filled the cache (a rate-limit countdown answered 10, 10, 10). They are now
  added to every miss, hit and 304 and never stored. A response with a cookie a
  dependency set, or with a dependency's `private` or `no-store` `Cache-Control`,
  is treated like one where the handler set it: it is not stored, and it is sent
  with that header or with `private`. A dependency's other `Cache-Control` and a
  header the handler sets over a dependency's are handled as the handler's own,
  on a hit too. A status code a dependency sets is sent, and keeps the response
  out of the backend. ([#233](https://github.com/allen0099/FastAPI-CacheX/issues/233))
- **A cancelled `CacheLock.acquire()` no longer leaves the lock held by nobody.**
  When `acquire()` was cancelled (a request timeout, a client disconnect) or
  failed while its claim was in flight, the backend could already have stored
  it, and every other caller was blocked until the lock's `ttl` ran out. The
  claim is now withdrawn with `delete_if_equals` before the exception
  propagates. ([#234](https://github.com/allen0099/FastAPI-CacheX/issues/234))
- **A cache miss answers a matching `If-None-Match` with 304.** When the entry
  had expired, been cleared or evicted, or was never stored (or is held by
  another worker), a client revalidating its copy got a full 200 with the same
  ETag; it now gets a 304, as on a hit. The handler still runs and the entry is
  stored again. Every 304 for a response the handler just rendered (a miss, a
  bypassed request, a `no_cache` route) now repeats the handler's `Set-Cookie`
  lines and runs its background task; both used to be dropped (#233). ([#237](https://github.com/allen0099/FastAPI-CacheX/issues/237))
- **`MemoryBackend` no longer lets callers change cached entries in place.** It
  stores and returns copies of each `CacheEntry`, so assigning to an entry read
  with `get()` or `get_cache_data()`, or to one after passing it to `set()`,
  `set_if_absent()` or `set_if_equals()`, no longer changes what later reads
  see. Redis and Memcached already behaved this way. ([#238](https://github.com/allen0099/FastAPI-CacheX/issues/238))
- **`MemcachedBackend` checks `key_prefix` when it is built.** A prefix with
  whitespace, control characters or non-ASCII, or one of 250 bytes or more,
  made every call fail with `MemcacheIllegalInputError`, because the prefix stays
  in front of the digest of a hashed key. Such a prefix now raises `ValueError`,
  and one over 186 bytes, which leaves no room for the digest, warns. ([#239](https://github.com/allen0099/FastAPI-CacheX/issues/239))
- **Warnings raised through `CacheManager` or a base-class fallback name your
  code.** The `FutureWarning` for a third-party backend whose `delete()` returns
  `None` pointed at `manager.py` or `base.py` when it was raised through
  `CacheManager` or a fallback such as `get_and_delete()`; it now names the line
  in your application, as the Memcached `RuntimeWarning`s already did. ([#333](https://github.com/allen0099/FastAPI-CacheX/issues/333))
- **A bare `@cache()` no longer sends an empty `Cache-Control` header.** With
  neither `ttl` nor a directive, the decorator has no directive of its own: it
  keeps the handler's `Cache-Control`, or sends none, and adds only the ETag. A
  response that sets a cookie or answers a request with credentials is still
  sent with `private`. ([#363](https://github.com/allen0099/FastAPI-CacheX/issues/363))
- **A counter that would overflow raises `CacheXError` on every backend.**
  Incrementing past the counter's range raised a raw `redis.exceptions.ResponseError`
  on Redis, wrapped around silently on Memcached and grew past 64 bits on the
  memory backend. Each backend now raises `CacheXError` and leaves the counter
  unchanged (Memcached undoes the wrap with a second `INCR`, so a concurrent
  increment on the same key can still see the wrapped value); memory and the base fallback use Redis's signed 64-bit range. Redis
  also returned values past 2**53 rounded, because its increment script passed
  them through a Lua number; it now returns them exactly. ([#364](https://github.com/allen0099/FastAPI-CacheX/issues/364))

## [0.4.0] - 2026-10-02

0.4.0 contains breaking changes; read
[Migrating to 0.4.0](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/)
before upgrading. Sessions and OAuth state are deprecated and will be removed
in 0.5.0. Three session changes announced in 0.3.9 are not made as announced:
see the entries for #75, #131 and #377.

### Added

- **`set_if_equals(key, expected, value, ttl=None)` on every backend.** It stores
  `value` only while `key` still holds `expected`, atomically on the memory,
  Redis and Memcached backends. `BaseCacheBackend` provides a non-atomic
  fallback for custom backends. ([#128](https://github.com/allen0099/FastAPI-CacheX/issues/128))
- **`logout()` ends a session, and `login()` takes `keep=`.**
  `await logout(request)` (in `fastapi_cachex.session`) deletes the session from
  the backend at once and expires the cookie, returning `False` when no session was
  loaded or started in the request. `login(request, user, keep=["cart"])` carries only the
  listed keys of the session the request arrived with over to the logged-in one;
  `keep=[]` carries none. Both need `FastAPICacheXSessionMiddleware`. ([#256](https://github.com/allen0099/FastAPI-CacheX/issues/256))
- **`CacheKey` builds, encodes and parses HTTP cache keys.**
  `CacheKey.from_request(request, *components, sort_query=...)` gives the key
  `@cache` stores a request under, `to_str()` the string the backend holds, and
  `CacheKey.parse(key)` decodes a stored key back into `method`, `host`, `path`,
  `query` and `extra`, or returns `None` for a key that is not an HTTP key.
  `build_cache_key()`, `clear_path()` and the monitoring routes all go through
  it, so the key format is defined in one place. ([#270](https://github.com/allen0099/FastAPI-CacheX/issues/270))

### Changed

- **`BaseCacheBackend.delete()` returns whether a key was removed.** The memory,
  Redis and Memcached backends return `True` when the key held an entry and
  `False` otherwise; on memory an entry that had already expired counts as
  absent, as it does on Redis. The non-atomic fallbacks on the base class use the
  result: `delete_many()` counts only the keys that existed, and
  `get_and_delete()` and `delete_if_equals()` let a caller win only if its delete
  removed the key. A third-party backend whose `delete()` still declares
  `-> None` fails type checking; at runtime the fallbacks count `None` as
  removed, as 0.3.x did, and emit a `FutureWarning`, and 0.5.0 treats it as
  `False`. See the [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#backend-delete). ([#71](https://github.com/allen0099/FastAPI-CacheX/issues/71))
- **Query parameters are sorted by name in HTTP cache keys by default.**
  `sort_query` defaults to on in `@cache`, `build_cache_key()`,
  `CacheKey.from_request()` and `invalidate()`, so `?b=2&a=1` and `?a=1&b=2`
  share one entry; repeated values of one name keep the order sent. A handler
  whose response depends on the query order as sent should set
  `@cache(sort_query=False)` and pass the same to `invalidate()`. Passing
  `sort_query` together with a custom `key_builder` now raises `CacheXError`,
  `False` included; call `build_cache_key(request, ..., sort_query=False)` in the
  builder instead. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#cache-keys). ([#72](https://github.com/allen0099/FastAPI-CacheX/issues/72))
- **`token_source_priority` does not have to name `"cookie"`, and the 0.3.9
  `FutureWarning` is gone.** 0.3.9 announced that 0.4.0 would read the session
  cookie only when the list names it. With sessions deprecated, that change is
  not made: the cookie is still read after the header sources, and `"cookie"` is
  still accepted only as the last entry. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#token-source-priority). ([#75](https://github.com/allen0099/FastAPI-CacheX/issues/75))
- **A header the handler sends more than once is cached and replayed line by
  line.** `CacheEntry.headers` is a tuple of `(name, value)` pairs in the order
  sent instead of `dict[str, str] | None`, so several `Link` lines, or a custom
  header read with `getlist()`, come back from the cache as they were sent, and a
  `304` repeats every `Vary` line; `vary=` adds its names on a new line instead
  of rewriting the first one. Building a `CacheEntry` still accepts a
  `dict`; code that reads `headers` as a `dict` needs updating. Redis and
  Memcached still decode entries with the old header object. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#cache-entry-headers). ([#105](https://github.com/allen0099/FastAPI-CacheX/issues/105))
- **`UserSessionDep` requires a session with a user.** It now resolves through
  `require_user_session`, like `AuthenticatedSession`, so an anonymous session
  gets `401` instead of passing. Routes that should keep admitting anonymous
  sessions use `SessionDep`. ([#127](https://github.com/allen0099/FastAPI-CacheX/issues/127))
- **Session saves are conditional.** The middleware's save of `request.session`
  changes, sliding renewal in `get_session()` and `update_session()` store a
  session only while the backend still holds what the request last read or
  wrote, so a request cannot bring back a session that another request deleted,
  invalidated or rotated. A dropped save is logged and the response is sent
  without a session token, unless the request already stored a renewal for a
  session that is still valid. When two requests change the same session at once,
  the first save wins. `update_session()` returns `True` or `False`. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#session-writes). ([#128](https://github.com/allen0099/FastAPI-CacheX/issues/128))
- **Rotating a session ID refuses a session that ended meanwhile.**
  `regenerate_session_id()` removes the old record atomically and raises
  `SessionNotFoundError` or `SessionInvalidError`, storing nothing under a new
  ID, if another request deleted, invalidated or rotated the session since it
  was read. `rotate_session_id()` answers `401` in that case and sends no token,
  and `login()` starts a new session for the user without the ended session's
  data. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#session-writes). ([#128](https://github.com/allen0099/FastAPI-CacheX/issues/128))
- **A JWT HMAC secret shorter than the hash output is rejected.** With
  `token_format="jwt"`, `SessionManager` raises `ValueError` when it builds its
  serializer if `jwt_algorithm` is `HS384` or `HS512` and `secret_key` is
  shorter than 48 or 64 bytes in UTF-8 (RFC 7518 section 3.2). 0.3.x only
  warned. Use a longer key or `HS256`. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#jwt-secret). ([#129](https://github.com/allen0099/FastAPI-CacheX/issues/129))
- **`get_session_manager` keeps returning the middleware's manager, and the 0.3.9
  `FutureWarning` is gone.** 0.3.9 announced that 0.4.0 would resolve it through
  `SessionManagerProxy`. With sessions deprecated, that change is not made. See
  the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#get-session-manager). ([#131](https://github.com/allen0099/FastAPI-CacheX/issues/131))
- **The session cookie defaults to `__Host-session` with the `Secure` flag, and
  `SessionConfig` rejects cookie settings browsers would refuse.** `SessionConfig.cookie_name`
  defaults to `"__Host-session"` and `cookie_https_only` to `True`, so a planted
  cookie from a subdomain or over plain HTTP is refused by the browser. The new
  name logs every cookie session out once after the upgrade. For plain-HTTP
  development, set `cookie_name="session", cookie_https_only=False`. A
  `__Host-` name without `Secure`, with a `cookie_path` other than `"/"` or with
  a `cookie_domain`, and a `__Secure-` name without `Secure`, raise a
  `ValidationError` instead of a `UserWarning`; because of the new default name,
  so does changing only one of those settings. The middleware's `FutureWarning`
  about the defaults is removed. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#session-cookie). ([#256](https://github.com/allen0099/FastAPI-CacheX/issues/256))
- **`Session.user` is read-only.** Assigning it raises `AttributeError`: log a
  user in with `login(request, user)` under `FastAPICacheXSessionMiddleware`,
  which also gives the session a new ID, or create the session with
  `SessionManager.create_session(user=...)`. A `user` given when a `Session` is
  built is still accepted. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#login-logout). ([#256](https://github.com/allen0099/FastAPI-CacheX/issues/256))
- **The host in an HTTP cache key is normalised.** It is lower-cased, and an
  empty port or the scheme's default one (`:80` for http, `:443` for https) is
  dropped, so `Example.com`, `example.com:80` and `example.com` share one entry
  instead of three. IPv6 literals keep their brackets. Keys for hosts written
  with upper case or a default port change, so those entries are cached afresh
  once. ([#265](https://github.com/allen0099/FastAPI-CacheX/issues/265))
- **HTTP cache keys start with the format tag `http:v2`.** A key is now
  `http:v2|method|host|path|query`, and `CacheKey.FORMAT_TAG` holds the tag, so a
  later format change never collides with these keys and
  `clear_pattern("http:v2|*")` removes every one of them. `clear_path()` and the
  monitoring routes only recognise tagged keys: a custom `key_builder` that does
  not use `build_cache_key()` still caches, but those two no longer see its keys. ([#266](https://github.com/allen0099/FastAPI-CacheX/issues/266))
- **A query string over 200 bytes is stored in the HTTP cache key as its
  SHA-256 digest.** The key holds `sha256:` and the 64-digit hex digest instead
  of the query, so a client cannot make the query part of the key arbitrarily
  long. The path stays readable, so `clear_path()` still finds such entries, and
  the monitoring routes show the digest as `query_params`. ([#269](https://github.com/allen0099/FastAPI-CacheX/issues/269))
- **HTTP cache keys are separated by a single `|` instead of `|||`.**
  `CACHE_KEY_SEPARATOR` is now `"|"`. Every client-controlled component is
  percent-encoded, so one character is enough. Entries written by 0.3.x are no
  longer read: each is a cache miss once and expires on its TTL, or remove them
  with `clear_pattern("*|||*")` on Redis and memory. `clear_pattern()` patterns
  that spell out `|||` need rewriting; see
  [Migrating to 0.4.0](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#cache-keys). ([#271](https://github.com/allen0099/FastAPI-CacheX/issues/271))
- **`CacheManager.get_or_set()` uses stampede protection by default.**
  `CacheManager(lock=...)` defaults to `True`, including the manager `AppCache`
  creates, so concurrent misses of one key run `factory` once while the other
  callers wait for its result. A miss costs six backend round trips instead of
  two on Redis and Memcached, and each waiting caller polls; a hit is unchanged.
  Pass `lock=False` to `get_or_set()` or `CacheManager()` to keep computing on
  every miss. `CacheManager(lock=None)` now raises `TypeError`. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#get-or-set-lock). ([#280](https://github.com/allen0099/FastAPI-CacheX/issues/280))
- **`add_routes()` requires `dependencies`, and content previews are off by
  default.** The monitoring routes have no authentication of their own, so
  `dependencies` is now a required keyword-only argument: pass your guard, or
  `dependencies=[]` to mount the routes unguarded on purpose. Leaving it out, or
  passing `None`, raises `TypeError`. `include_content_preview` is keyword-only
  and defaults to `False`, also for apps that already pass a guard; pass `True`
  to keep the first 100 bytes of each cached body in `/cached-records`. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#add-routes). ([#298](https://github.com/allen0099/FastAPI-CacheX/issues/298))

### Deprecated

- **`SessionConfig.use_bearer_token` is removed in 0.5.0, not 0.4.0.** It keeps
  working and keeps its `DeprecationWarning`, and goes with
  `fastapi_cachex.session`; list the token sources in `token_source_priority`
  instead. See the
  [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#token-source-priority). ([#377](https://github.com/allen0099/FastAPI-CacheX/issues/377))
- **Sessions and OAuth state are deprecated and will be removed in 0.5.0.**
  FastAPI-CacheX is narrowing to HTTP and application caching. Importing
  `fastapi_cachex.session` or `fastapi_cachex.state`, or reading one of their
  names from the `fastapi_cachex` package, emits a `FutureWarning`; a plain
  `import fastapi_cachex` does not. The names are no longer in
  `fastapi_cachex.__all__`. Both packages get security fixes only until 0.5.0;
  the [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#session-state-deprecated) says where to move. ([#420](https://github.com/allen0099/FastAPI-CacheX/issues/420))

### Removed

- **The deprecated header-only `SessionMiddleware` is removed.** It was
  deprecated in favour of `FastAPICacheXSessionMiddleware` since 0.3.1, which
  reads the same custom header and `Authorization: Bearer` token and adds
  `request.session` and the session cookie. Replace
  `app.add_middleware(SessionMiddleware, ...)` with
  `app.add_middleware(FastAPICacheXSessionMiddleware, ...)` and set the cookie
  options (see the [migration guide](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/#session-middleware)). ([#69](https://github.com/allen0099/FastAPI-CacheX/issues/69))
- **`BackendProxy.get_backend()` and `BackendProxy.set_backend()` are removed.**
  They had emitted a `DeprecationWarning` since 0.3.0. Call `BackendProxy.get()`
  and `BackendProxy.set(backend)` instead. ([#70](https://github.com/allen0099/FastAPI-CacheX/issues/70))
- **Redis `clear_pattern()` no longer retries a pattern with the key prefix
  stripped.** A pattern always matches the logical key, as on every other
  backend, so one that starts with the backend's `key_prefix` now clears only
  logical keys that themselves start with it, and the `DeprecationWarning` is
  gone. Leave the prefix out: `clear_pattern("fastapi_cachex:GET|||*")` becomes
  `clear_pattern("http:v2|GET|*")`. ([#125](https://github.com/allen0099/FastAPI-CacheX/issues/125))
- **The Redis `encoding` option is removed; the client reads raw bytes.**
  `AsyncRedisCacheBackend` no longer takes `encoding` or `decode_responses` and
  raises `TypeError` for either. `RedisConfig` has no `encoding` field and now
  ignores one like any other unknown field. Replies go straight to the entry
  codec. Entries were always UTF-8 JSON, so data stored with the default
  encoding reads back unchanged. Remove the argument:
  `AsyncRedisCacheBackend(host="redis", encoding="utf-8")` becomes
  `AsyncRedisCacheBackend(host="redis")`. ([#126](https://github.com/allen0099/FastAPI-CacheX/issues/126))
- **`fastapi_cachex.exceptions.CacheError` is removed.** Nothing in the package
  ever raised it, and it had emitted a `DeprecationWarning` since 0.3.8. Catch
  `CacheXError` instead. ([#130](https://github.com/allen0099/FastAPI-CacheX/issues/130))
- **The deprecated `memcache` extra is removed; install `fastapi-cachex[memcached]`.**
  The failure is quiet: pip and uv only warn about an unknown extra, so
  `fastapi-cachex[memcache]` still installs, but without `pymemcache`, and
  `MemcachedBackend` raises when it is constructed. Replace `[memcache]` with
  `[memcached]` in your requirements. ([#202](https://github.com/allen0099/FastAPI-CacheX/issues/202))
- **`CACHE_KEY_MIN_PARTS`, `CACHE_KEY_MAX_SPLIT` and `CACHE_KEY_MAX_PARTS` are
  removed from `fastapi_cachex.routes`.** They described how the monitoring
  routes split a key, which `CacheKey.parse()` now does. Use
  `CacheKey.parse(key)` to read a key's components. ([#270](https://github.com/allen0099/FastAPI-CacheX/issues/270))

## [0.3.9] - 2026-09-29

0.3.9 is the last 0.3.x release. 0.4.0 contains breaking changes; see [Migrating to 0.4.0](https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_4/).

### Added

- **`CacheManager.get_or_set()` supports lock-based stampede protection.** Pass
  `lock=True` (or configure `lock=True` on `CacheManager`) to coordinate
  concurrent misses for the same key through `CacheLock` so only one caller
  executes `factory` while others wait for the cached value. `LockTimeoutError`
  now also subclasses the standard library `TimeoutError`, so `except TimeoutError`
  catches it. ([#66](https://github.com/allen0099/FastAPI-CacheX/issues/66))
- **Every backend can be closed with `aclose()` and used with `async with`.**
  `AsyncRedisCacheBackend.aclose()` closes the redis-py client and the connection
  pool it created, and `MemcachedBackend.aclose()` closes every pooled socket, so
  the lifespan pattern in the backends guide works for all three backends instead
  of raising `AttributeError` on Redis and Memcached. `BaseCacheBackend` gains a
  no-op `aclose()` that custom backends may override, and `__aenter__`/`__aexit__`
  that close the backend when the block ends. Calling `aclose()` twice is safe; a
  Redis `connection_pool=` you pass in is left for you to close. ([#243](https://github.com/allen0099/FastAPI-CacheX/issues/243))
- **`build_cache_key(request, *components)` builds the default cache key plus
  extra components.** A custom `key_builder` that adds a user ID, tenant or
  locale no longer rebuilds `method|||host|||path|||query` by hand: with no
  components the helper returns exactly the default key (existing entries keep
  their keys), and each `str` or `int` component is appended after the query
  string, percent-encoded like the host and path so it cannot inject the
  separator. `clear_path()` on the memory and Redis backends still clears such
  keys by path, and without `include_params` no longer mistakes the extra
  components for a query string; the monitoring routes report them, decoded, in
  a new `extra_components` field. The per-user example in HTTP_CACHING.md
  ("Authenticated endpoints") now uses it. `default_key_builder` stays and
  returns `build_cache_key(request)`. ([#264](https://github.com/allen0099/FastAPI-CacheX/issues/264))
- **Add `@cache(sort_query=True)` so reordered query strings share one entry.**
  The default key builder then orders the query parameters by name, so
  `?a=1&b=2` and `?b=2&a=1` hit the same entry. The sort is stable: repeated
  values of one name keep the order the client sent, so `?tag=b&tag=a` and
  `?tag=a&tag=b` stay distinct, and names and values are encoded exactly as in
  the unsorted key. The default `False` leaves every existing key unchanged.
  Combining it with a custom `key_builder` raises `CacheXError`; such a builder
  can call `build_cache_key(request, sort_query=True)` instead.
  `invalidate()` takes the same `sort_query` keyword. ([#267](https://github.com/allen0099/FastAPI-CacheX/issues/267))
- **`@cache(vary=[...])` caches one entry per value of the listed request
  headers.** A route whose response depends on `Accept-Language` or `Accept` no
  longer serves the first cached variant to everyone: each listed header adds a
  `name=value` component (name lower-cased, value trimmed, missing as empty) to
  the key, after whatever the `key_builder` returns, and the names are added to
  the `Vary` header of every GET response, 200 or 304, stored or not, without
  repeating names already there and leaving `Vary: *` alone. `vary` is checked
  when the decorator is applied; a bare string such as `vary="Accept"` is
  rejected. `invalidate()` takes the same `vary` list, and `clear_path()` clears
  every variant of a path. Routes without `vary` keep their keys. The credential
  headers `Authorization`, `Proxy-Authorization`, `Cookie` and `X-Session-Token`
  are keyed on `sha256:` plus the SHA-256 of their value, so no token or session
  cookie shows up in `get_all_keys()`, the monitoring routes or the Redis and
  Memcached keyspace; missing or empty, they stay `name=` so anonymous callers
  share one entry. Listing `Cookie` emits a `UserWarning` when the decorator is
  applied, since every visitor then gets an entry of their own. The header
  values are client-controlled, so HTTP_CACHING.md shows how to normalise them
  with a `key_builder` and `build_cache_key` instead. ([#268](https://github.com/allen0099/FastAPI-CacheX/issues/268))
- **`login(request, user)` logs a user in through `FastAPICacheXSessionMiddleware`.**
  It gives the request's session a new ID against session fixation, or starts a
  new session for a visitor who has none, and attaches the `SessionUser`, so a
  later request with the new token passes `require_user_session` /
  `AuthenticatedSession`. A session that belonged to a different user is
  deleted and replaced by a new one, so none of its data reaches the new user.
  The middleware sends that token through the request's transport (cookie, header or `Authorization: Bearer`) with
  `Cache-Control: private, no-store`. Before, a login needed
  `rotate_session_id()`, then `update_session()` to save the user, and for a new
  visitor a hand-built cookie; writing `request.session["user_id"]`, as the
  `rotate_session_id()` docstring showed, never attached a user at all.
  `login()` raises `RuntimeError` outside `FastAPICacheXSessionMiddleware`.
  `examples/session_login.py` and the session guide now use it. ([#293](https://github.com/allen0099/FastAPI-CacheX/issues/293))
- **Added a security policy.** `SECURITY.md` explains how to report a
  vulnerability privately through GitHub private vulnerability reporting instead
  of a public issue, which release line gets security fixes (the latest 0.3.x)
  and what to include in a report. It is linked from the contributing guide and
  the README. ([#300](https://github.com/allen0099/FastAPI-CacheX/issues/300))
- **`@cache` warns once when a credential makes a route bypass the backend.**
  The first request that bypasses the shared backend on a route because it
  carried an `Authorization` header, a session token or non-empty
  `request.session` data is logged at `WARNING` on the `fastapi_cachex.cache`
  logger, once per route and credential kind, naming the route template and the
  credential (never its value). The message points to `public=True` for a
  response that is the same for every user (which also sends `Cache-Control:
  public` downstream) and to `cache_authorized=True` with a per-user
  `key_builder` for a per-user one. Each bypass is still logged at `DEBUG`.
  `docs/HTTP_CACHING.md` gains a "Requests with credentials" section on choosing
  between the two. ([#326](https://github.com/allen0099/FastAPI-CacheX/issues/326))

### Changed

- **The implicit `MemoryBackend` fallback now logs a warning.** When `@cache`,
  `CacheBackend` or `AppCache` registers a `MemoryBackend` because no backend was
  set, the `fastapi_cachex.proxy` logger logs a `WARNING` once per process: the
  cache is per process, so under multiple workers an invalidation reaches only
  one worker. Configure a backend with `BackendProxy.set(...)` at startup to
  silence it; an explicit `BackendProxy.set(MemoryBackend())` does not warn. The
  fallback used to be logged only at `DEBUG`. ([#327](https://github.com/allen0099/FastAPI-CacheX/issues/327))

### Deprecated

- **Setting `token_source_priority` without `"cookie"`.** In 0.4.0 the list names
  every token source. Its default becomes `["header", "bearer", "cookie"]`, the
  order used today, and a list without `"cookie"` means the middleware neither
  reads nor sets the session cookie. The list now accepts `"cookie"` as its last
  entry, which changes nothing yet since the cookie is read there anyway; any
  other position raises a `ValidationError`. `FastAPICacheXSessionMiddleware`
  now emits a `FutureWarning` when the list was set explicitly without
  `"cookie"`: add it as the last entry to keep the cookie. The default list does
  not warn. See "Migrating to 0.4.0" in the docs. ([#75](https://github.com/allen0099/FastAPI-CacheX/issues/75))
- **The `encoding` option of `AsyncRedisCacheBackend` and `RedisConfig`.** 0.4.0
  removes it and reads raw bytes; entries were always UTF-8. Passing a UTF-8
  `encoding` to `AsyncRedisCacheBackend`, or setting `encoding` on a
  `RedisConfig` given to `load_from_config()`, now emits a `DeprecationWarning`.
  Any other value passed to `AsyncRedisCacheBackend` gets no
  `DeprecationWarning`: it keeps its `RuntimeWarning`, which already announces the
  removal. Leave it out: UTF-8 is what you get without it. ([#126](https://github.com/allen0099/FastAPI-CacheX/issues/126))
- **JWT HMAC secrets shorter than the hash output.** The `UserWarning` that
  `JWTTokenSerializer` emits for an `HS384` key under 48 bytes or an `HS512` key
  under 64 bytes now says that 0.4.0 will reject such a key at startup. ([#129](https://github.com/allen0099/FastAPI-CacheX/issues/129))
- **`get_session_manager` finding the manager only on `app.state`.** 0.4.0
  resolves `get_session_manager` (and `SessionManagerDep`, `ClientIPDep` and
  `rotate_session_id()`, which use it) through `SessionManagerProxy` only. It now
  emits a `FutureWarning`, once per app, when the proxy holds no manager or a
  different one than the session middleware. Call
  `SessionManagerProxy.set(session_manager)` at startup; the middleware can then
  pick the manager up from the proxy. ([#131](https://github.com/allen0099/FastAPI-CacheX/issues/131))
- **Relying on the session cookie defaults of `FastAPICacheXSessionMiddleware`.**
  0.4.0 names the session cookie `__Host-session` and sets the `Secure` flag by
  default, so every cookie session is logged out once on upgrade and a plain-HTTP
  setup stops receiving the cookie. The middleware now emits a `FutureWarning`
  when its config leaves `cookie_name` or `cookie_https_only` at the default. Set
  both: `cookie_name="session", cookie_https_only=False` keeps the current cookie,
  `cookie_name="__Host-session", cookie_https_only=True` switches now. The
  warning does not depend on how clients send the token: a header-only app on
  this middleware warns too. See "Migrating to 0.4.0" in the docs. ([#256](https://github.com/allen0099/FastAPI-CacheX/issues/256))
- **`SessionConfig` with a `__Host-` or `__Secure-` cookie name that browsers
  refuse.** A `__Host-` name without `cookie_https_only=True`, with a
  `cookie_path` other than `"/"` or with a `cookie_domain`, and a `__Secure-` name
  without `cookie_https_only=True`, now emit a `UserWarning`: browsers drop such a
  cookie, so the session never sticks. 0.4.0 will reject these settings. ([#256](https://github.com/allen0099/FastAPI-CacheX/issues/256))
- **Relying on the `lock=False` default of `CacheManager.get_or_set()`.** 0.4.0
  turns stampede protection on by default. A `get_or_set()` call that passes no
  `lock=`, on a manager created without `lock=` (including the one `AppCache`
  creates), now emits a `FutureWarning` once per manager. Pass `lock=False` to
  keep the current behaviour or `lock=True` to opt in now, per call or to
  `CacheManager(...)`; for `AppCache`, register a manager with
  `CacheManagerProxy.set()`. `CacheManager(lock=None)` is now accepted and means
  "not chosen". ([#280](https://github.com/allen0099/FastAPI-CacheX/issues/280))
- **Calling `add_routes()` without `dependencies`.** The monitoring routes have
  no access control of their own and expose every cached key (including query
  strings) and response previews, so leaving `dependencies` unset now emits a
  `UserWarning`. 0.4.0 will require the parameter and turn
  `include_content_preview` off by default
  ([#298](https://github.com/allen0099/FastAPI-CacheX/issues/298)). Pass a guard
  such as `dependencies=[Depends(verify_admin)]`, or `dependencies=[]` to keep
  the routes open on purpose without the warning. ([#301](https://github.com/allen0099/FastAPI-CacheX/issues/301))
- **`SessionConfig.use_bearer_token`.** `token_source_priority` already decides
  whether bearer tokens are read, and 0.4.0 removes the flag. Passing it now
  emits a `DeprecationWarning`, whatever its value. Replace
  `use_bearer_token=False` by leaving `"bearer"` out of the list (for example
  `token_source_priority=["header", "cookie"]`); `use_bearer_token=True` is the
  default and can be dropped. ([#377](https://github.com/allen0099/FastAPI-CacheX/issues/377))

### Fixed

- **`CacheManager.clear_pattern()` matches the manager's `key_prefix` literally.**
  The prefix used to be passed to the backend as part of the glob, so glob
  characters in it were live: `key_prefix="cache[1]:"` missed its own keys, and
  `key_prefix="a?:"` also cleared the keys of a manager with prefix `ab:`. A
  prefix without `*`, `?`, `[`, `]` or `\` still goes to the backend's native
  `clear_pattern()` as before. A prefix with one makes `clear_pattern()` list
  every key and match the rest of the key against `pattern` with
  `fnmatch.fnmatchcase`, which is slower on Redis and uses fnmatch rather than
  Redis glob syntax for `pattern`. Constructing a `CacheManager` with such a
  prefix emits a `UserWarning`. ([#140](https://github.com/allen0099/FastAPI-CacheX/issues/140))
- **`CacheManager.get_or_set()` returns the same value on a miss as on a hit.**
  On a miss it used to return the object the factory produced, and on a later
  hit the JSON-decoded copy, so a tuple came back as a tuple and then as a list,
  and `{1: "a"}` as itself and then as `{"1": "a"}`. A miss now encodes the value
  once, stores those bytes and returns them decoded. A value JSON cannot encode
  (`datetime`, `Decimal`, `UUID`, a pydantic model) still raises `TypeError`
  after the factory has run, and nothing is stored. ([#235](https://github.com/allen0099/FastAPI-CacheX/issues/235))
- **Responses served from the cache carry an `Age` header, so downstream caches no longer keep them for up to twice the `ttl`.**
  A hit, and a 304 answered from the stored entry's ETag, used to send
  `Cache-Control: max-age=<ttl>` with no `Age`, so a browser or CDN restarted
  the freshness clock on every hit. They now send `Age`, the whole seconds since
  the entry was stored, clamped to `0`–`ttl` against clock skew between hosts;
  downstream subtracts it from `max-age` (RFC 9111 §4.2.3). `CacheEntry` has a
  new optional `stored_at` field (epoch seconds, wall clock) that `@cache` sets
  and the Redis/Memcached codec stores. Entries written by older releases decode
  with `stored_at=None` and are served without `Age`; responses the handler
  renders (misses, `no_cache`, bypassed requests) never carry one. An `Age`
  header the handler sets is no longer stored and replayed. ([#254](https://github.com/allen0099/FastAPI-CacheX/issues/254))
- **`MemcachedBackend.increment()` no longer fails when a new short-lived counter expires mid-call.**
  Creating a counter takes an `ADD` and then an `INCR`, and Memcached keeps time
  in whole seconds, so a counter created with `ttl=1` could expire between the
  two and `increment()` raised `CacheXError("Counter vanished between ADD and
  INCR")`. The `ADD` + `INCR` pair is now retried up to 16 times in the same
  worker call, starting a new window at `delta`; `CacheXError` is raised only if
  the counter vanishes on every attempt. ([#315](https://github.com/allen0099/FastAPI-CacheX/issues/315))
- **`MemcachedBackend.clear_path()` warns on every call, not only with `include_params=True`.** Memcached cannot enumerate keys, so `clear_path()` deletes only a key named exactly as the path and never an HTTP cache entry; the default call used to return `0` silently, leaving a response cached after a write. The warning and BACKENDS.md now point to `invalidate(request)`, which drops a cached route's entry on every backend. ([#320](https://github.com/allen0099/FastAPI-CacheX/issues/320))
- **An `async` key builder is rejected when `@cache` is applied instead of failing every request with a 500.**
  `@cache(key_builder=...)` accepted an `async def` builder (or an object with an
  `async def __call__`, or a `functools.partial` of either) and then raised
  `TypeError` on every request, plus a "coroutine was never awaited" warning. It
  now raises `CacheXError` at decoration time, and `invalidate(request,
  key_builder=...)` raises it before touching the backend. A builder that returns
  anything but a `str` raises `CacheXError` on the request, whatever `fail_open`
  says. Key builders must be sync functions returning `str`. ([#323](https://github.com/allen0099/FastAPI-CacheX/issues/323))
- **`StateManager` treats more kinds of malformed stored state as malformed.**
  A stored value that is JSON but not an object, such as `[1, 2]` or `"x"`, used
  to escape as `TypeError`, and one holding an integer longer than Python's
  digit limit (4300 by default) as `ValueError`. Now `consume_state()` raises
  `StateDataError`, `validate_state()` returns `False` and
  `get_state_metadata()` returns `None`, as documented for malformed data. ([#368](https://github.com/allen0099/FastAPI-CacheX/issues/368))
- **Clearer warnings.** The `FutureWarning` from `get_session_manager()`
  now says whether `SessionManagerProxy` is empty or holds a different manager,
  the `UserWarning` for a `__Host-`/`__Secure-` cookie name the browser would
  refuse links to the 0.4.0 issue, and the Memcached `RuntimeWarning`s of
  `clear()`, `clear_path()`, `clear_pattern()` and `get_all_keys()` name the
  application's line when raised through `CacheManager` or `SessionManager`,
  rather than a line in the library. ([#386](https://github.com/allen0099/FastAPI-CacheX/issues/386))

### Security

- **`@cache` no longer stores responses that belong to one caller.** A request
  with an `Authorization` header now bypasses the backend like `private=True`
  (RFC 9111 §3.5), unless the route is `public=True` or opts in with the new
  `cache_authorized=True`, meant for a `key_builder` that includes the verified
  caller's identity. A response whose own `Cache-Control` contains `private` or
  `no-store`, or that sets a cookie, is served but not stored, and the handler's
  `private`/`no-store` header is no longer replaced by the decorator's. A cookie
  response and a bypassed `Authorization` response are sent with `private` in
  place of `public` (keeping the other directives; `private, no-cache` on
  `no_cache` routes), so a CDN or proxy does not store them either;
  `must_revalidate=True` does not lift the bypass. Previously all three were
  stored and replayed to every caller, so one user's response could reach
  another. The per-user example in HTTP_CACHING.md ("Authenticated endpoints")
  now passes `cache_authorized=True`. ([#296](https://github.com/allen0099/FastAPI-CacheX/issues/296))
- **Responses that carry a session token are never cacheable.** When
  `FastAPICacheXSessionMiddleware` sends a token (a new session, a sliding
  renewal, a regenerated ID) or a cookie-clearing `Set-Cookie`, it now sets
  `Cache-Control: private, no-store`, replacing whatever the route set, and adds
  `Vary` for the token transport even when the handler never touched
  `request.session`. Before, a `@cache(public=True)` route could return
  `Cache-Control: public` with a valid session cookie, and a CDN or reverse proxy
  could hand that cookie to the next visitors. The deprecated `SessionMiddleware`
  does the same when it sends a token, and neither middleware repeats a `Vary`
  name the response already has. ([#297](https://github.com/allen0099/FastAPI-CacheX/issues/297))
- **`@cache` backend-failure warnings no longer log the cache key.** The
  "Cache backend read failed" and "Cache backend write failed" warnings wrote
  the full key, which holds the raw query string (`?token=...`, `?code=...`,
  e-mail addresses), `vary` header values and any `build_cache_key` components,
  into application logs. They now log the method, the path (formatted with `%r`,
  so a control character in it is escaped) and `key_ref`, the first 12 hex
  digits of the key's SHA-256, the same digest format as the OAuth state logs;
  the full key is logged at `DEBUG` under the same `key_ref`.
  `CacheManager.get()`'s "Failed to decode cached value" warning likewise logs
  `key_ref` instead of the developer's key, which often embeds user IDs or
  e-mail addresses. ([#299](https://github.com/allen0099/FastAPI-CacheX/issues/299))
- **`@cache` no longer shares a response to a request that arrived with a
  session.** Only `Authorization` bypassed the backend, so a plain `@cache` on a
  route that read the session served the first visitor's response to everyone:
  the session token in `X-Session-Token` or the session cookie, and anonymous
  sessions (a cart in `request.session`), were not recognised. A request now
  bypasses the backend and is answered with `private`, as for `Authorization`,
  when the session middleware loaded a session for it (from any token transport,
  with or without a user) or `request.session` is non-empty under any session
  middleware. A token that resolves to no session does not count. `public=True`
  and `cache_authorized=True` lift the bypass as before. ([#319](https://github.com/allen0099/FastAPI-CacheX/issues/319))
- **`examples/session_login.py` no longer sends a new visitor's login token in a
  cacheable response.** When no session was loaded, the example set the cookie
  itself, and the middleware adds `Cache-Control: private, no-store` only to
  tokens it sends, so a shared cache could store the login and hand it to the
  next visitor. That branch now sends `Cache-Control: private, no-store`, sets the
  cookie with every configured `cookie_*` attribute (`domain` was missing, so the
  cookie cleared at logout did not match it), and keeps the token out of headers
  and the body so page scripts cannot read it; API clients get theirs from a
  token endpoint, as in `examples/session_jwt.py`. The session guide says the
  same for this case. ([#322](https://github.com/allen0099/FastAPI-CacheX/issues/322))
- **`@cache` routes without a positive `ttl` now send `private` to requests with
  credentials.** A route with `ttl=0` or no `ttl` skips the backend anyway, so it
  did not check for `Authorization` or a session, and it answered them with its
  plain `Cache-Control`: `@cache(ttl=0, must_revalidate=True)` sent `max-age=0,
  must-revalidate`, which RFC 9111 §3.5 lets a shared cache such as a CDN store
  and reuse for other users after revalidation. Such a response now gets `private`
  like on every other route (`private, max-age=0, must-revalidate`), unless the
  route is `public=True`. No bypass warning is logged
  for these routes, since they never read the backend. ([#362](https://github.com/allen0099/FastAPI-CacheX/issues/362))
- **`cache_authorized=True` routes now send `private` to requests with
  credentials, and session dependencies add `Vary`.** The option keys the backend
  entry on the caller, but a shared cache in front of the app keys on the URL
  alone, and the response kept the decorator's header: `max-age=60,
  must-revalidate` let a CDN serve one user's answer to the next under RFC 9111
  §3.5, and a session token in `X-Session-Token` or a cookie needs no directive
  at all. Such responses (200, cache hit and 304) now carry `private` in place of
  `public`, and the backend is still used. `get_session`, `get_optional_session`
  and the dependencies built on them now make `FastAPICacheXSessionMiddleware`
  (and the deprecated `SessionMiddleware`) add `Vary` for the token sources, as
  reading `request.session` already did. Routes that serve the same answer to
  every user should use `public=True`. ([#372](https://github.com/allen0099/FastAPI-CacheX/issues/372))
- **The session examples no longer fall back to a fixed placeholder key.** With
  `SESSION_SECRET_KEY` unset they emitted valid sessions signed with a key
  published in the repository, so a copied example that reached production
  without the variable accepted forged tokens. They now warn and sign with a
  random key made up for that run. ([#384](https://github.com/allen0099/FastAPI-CacheX/issues/384))

## [0.3.8] - 2026-09-27

### Added

- **`CacheLock`, a distributed lock built on backend primitives.** Usable as an async context manager or with direct `acquire`/`release`/`extend`/`locked` calls, raising `LockTimeoutError` on timeout. ([#64](https://github.com/allen0099/FastAPI-CacheX/issues/64))
- **`expire_if_equals()` backend primitive for owner-checked TTL renewal.** Added to `BaseCacheBackend`, `MemoryBackend`, `AsyncRedisCacheBackend`, and `MemcachedBackend`. ([#64](https://github.com/allen0099/FastAPI-CacheX/issues/64))

- **`memcached` extra for `MemcachedBackend`.** Install with
  `fastapi-cachex[memcached]`, matching the backend's name. It pulls in the
  same `pymemcache` dependency as the old `memcache` extra.
  ([#201](https://github.com/allen0099/FastAPI-CacheX/issues/201))

- **`MemoryBackend.aclose()` stops the cleanup task and waits for it.**
  `stop_cleanup()` only requests cancellation and stays as it is.
  ([#181](https://github.com/allen0099/FastAPI-CacheX/issues/181))
- **`ProxyNotSetError` for an unset manager proxy.** `CacheManagerProxy`,
  `SessionManagerProxy` and `StateManagerProxy` raise it from `get()` when no
  instance is set, instead of `BackendNotFoundError`, whose name points at a
  backend that is not involved. It subclasses `BackendNotFoundError`, so
  existing handlers keep catching it. `BackendProxy` is unchanged.
  ([#161](https://github.com/allen0099/FastAPI-CacheX/issues/161))
- **`CacheXError`, `BackendNotFoundError` and `RequestNotFoundError` are
  exported from `fastapi_cachex`.** They were only importable from
  `fastapi_cachex.exceptions`, unlike every session and state exception.
  ([#160](https://github.com/allen0099/FastAPI-CacheX/issues/160))
- **`require_user_session` / `AuthenticatedSession` require a logged-in
  user.** `get_session`, `RequiredSession` and `UserSessionDep` accept the
  anonymous session any visitor gets by writing to `request.session`; the
  new dependency also answers `401` when `session.user` is `None`.
  `UserSessionDep` keeps its behaviour until 0.4.0.
  ([#114](https://github.com/allen0099/FastAPI-CacheX/issues/114))
- **`/cached-records` reports each entry's `media_type`.** It is the media
  type the response was stored with, or `null` when it had none.
  `content_type` is still returned for compatibility but is always `"bytes"`.
  `CACHE_KEY_MAX_PARTS` in `fastapi_cachex.routes` is renamed
  `CACHE_KEY_MAX_SPLIT`, since it is a `maxsplit` count; the old name remains
  as an alias. ([#184](https://github.com/allen0099/FastAPI-CacheX/issues/184))

### Changed

- **GitHub release notes list one line per change.** Each changelog entry now
  opens with a bold one-line summary. The release page shows only those
  summaries with their issue links, grouped as in the changelog, and links to
  the full entries on the documentation site. The changelog itself keeps the
  details.
- **`SessionError` derives from `CacheXError`.** It derived from `Exception`,
  while `StateError` already derived from `CacheXError`, so `except CacheXError`
  caught state errors but not session errors. Handlers for `SessionError` or
  `Exception` keep working. A `try` block that lists `except CacheXError`
  before `except SessionError` now takes the `CacheXError` branch for session
  errors. ([#162](https://github.com/allen0099/FastAPI-CacheX/issues/162))
- **`@cache` serves uncached responses when the backend fails.** A backend
  error on read or write turned every cached route into a 500, even after the
  handler had produced a good response; this includes a healthy Memcached
  rejecting a response over its 1 MB item size. A failed read now counts as a
  miss and a failed write leaves the response unstored, each logged as a
  warning on `fastapi_cachex.cache`. `@cache(fail_open=False)` restores the
  old behaviour. `invalidate()`, `CacheManager`, `StateManager`, `CacheLock`
  and sessions still raise.
  ([#228](https://github.com/allen0099/FastAPI-CacheX/issues/228))
- **Redis clear operations delete page by page.** `clear()`, `clear_pattern()`
  and `clear_path()` delete each SCAN page as it arrives instead of first
  collecting every matching key in memory, and `clear_path()` no longer runs
  an extra `EXISTS` on the direct key.
  ([#172](https://github.com/allen0099/FastAPI-CacheX/issues/172))
- **Redis `get_cache_data()` no longer blocks the server.** It fetched every
  value and TTL inside one `MULTI`/`EXEC`, because redis-py pipelines are
  transactional by default. It now sends non-transactional pipelines of 100
  keys each.
  ([#171](https://github.com/allen0099/FastAPI-CacheX/issues/171))
- **Memcached runs each operation in one worker call, and `delete_many()`
  counts what it removed.** `increment`, `get_and_delete` and
  `delete_if_equals` used to hand every round trip to its own worker thread;
  `delete_many()` took one per key and returned how many keys it was given.
  Each call now makes a single trip, and `delete_many()` returns how many of
  the keys existed.
  ([#174](https://github.com/allen0099/FastAPI-CacheX/issues/174),
  [#176](https://github.com/allen0099/FastAPI-CacheX/issues/176))
- **`CacheBackend` falls back to a `MemoryBackend` like `@cache` and
  `AppCache`.** It used to answer 500 (`BackendNotFoundError`) until some
  `@cache` route had registered the fallback. The three lazy defaults
  (`get_backend_or_fallback`, `get_app_cache`, `get_state_manager`) now share
  `ProxyBase.get_or_create(factory)`, which runs the factory at most once
  under a per-class lock; `get_state_manager` could previously register two
  managers under concurrent first requests. States still have no memory
  fallback. ([#112](https://github.com/allen0099/FastAPI-CacheX/issues/112))
- **`@cache` builds the cache key only when it reads or writes the backend.**
  A custom `key_builder` is no longer called for `no_store`, `private` or
  TTL-less routes, where the key only fed debug logs.
  ([#182](https://github.com/allen0099/FastAPI-CacheX/issues/182))
- **Session lookups no longer write to the backend unless sliding expiration
  renewed the session.** `SessionManager.get_session()` used to save the
  session on every call to record `last_accessed`, so each authenticated
  request cost a write (two if it also modified the session). The stored
  `last_accessed` is now updated only when the session is written (created,
  modified, renewed or regenerated); pass `get_session(..., touch=True)` to
  save it on every lookup. Session entries also store a constant fingerprint
  instead of hashing the payload.
  ([#115](https://github.com/allen0099/FastAPI-CacheX/issues/115))
- **Runtime dependencies declare minimum versions.** `fastapi>=0.133.0`,
  `starlette>=1.0.0` (now declared directly; the session middleware needs
  Starlette 1.0), `pydantic>=2.7.0` and `itsdangerous>=1.1.0`; the extras
  require `pymemcache>=4.0.0` and `orjson>=3.4.7`. Older versions could be
  installed before but failed at import. A `lowest` tox env and CI workflow
  test every floor. ([#194](https://github.com/allen0099/FastAPI-CacheX/issues/194))

### Deprecated

- **Passing the Redis key prefix in a `clear_pattern()` pattern.** When such a
  pattern clears nothing, the prefix-stripped form is still tried and emits a
  `DeprecationWarning` if it clears anything. The retry will be removed in
  0.4.0. ([#125](https://github.com/allen0099/FastAPI-CacheX/issues/125))
- **The `memcache` extra.** Use `memcached` instead. The old name keeps working
  until 0.4.0 removes it; after that, pip and uv only warn about the unknown
  extra and install without `pymemcache`.
  ([#202](https://github.com/allen0099/FastAPI-CacheX/issues/202))
- **`fastapi_cachex.exceptions.CacheError`.** Nothing in the package raises it.
  Accessing or importing it emits a `DeprecationWarning`; catch `CacheXError`
  instead. It will be removed in 0.4.0.
  ([#163](https://github.com/allen0099/FastAPI-CacheX/issues/163))

### Fixed

- **Redis `clear_pattern()` no longer strips a pattern that starts with the key
  prefix.** The pattern now always matches the logical key, as on the memory
  backend. With `key_prefix="cache:"` and the default `CacheManager`, whose
  keys also start with `cache:`, `clear_pattern("user:*")` used to clear
  nothing. ([#109](https://github.com/allen0099/FastAPI-CacheX/issues/109))

- **The Redis backend warns when `encoding` is not UTF-8.** Entries are always
  written as UTF-8 JSON, but the client decoded replies with the configured
  encoding, so under `encoding="latin-1"` non-ASCII content came back
  corrupted without any error. `AsyncRedisCacheBackend` and
  `load_from_config()` now emit a `RuntimeWarning` for any encoding other than
  UTF-8; the parameter is removed in 0.4.0 (#126).
  ([#122](https://github.com/allen0099/FastAPI-CacheX/issues/122))

- **Counters are recognised the same way on every backend.** On the memory
  backend and the base-class fallback, `increment()` on a cached response
  whose body was a number, such as `42`, returned 43 and overwrote the
  response. It now raises `CacheXError`, as Redis and Memcached already did. A
  counter written with `set(key, counter_entry(n))` can now be incremented on
  Redis and Memcached too, because it is stored as a bare integer. Stored
  values such as `" 7"` or `"1_0"`, which `int()` accepts, are no longer
  read as counters.
  ([#111](https://github.com/allen0099/FastAPI-CacheX/issues/111))

- **`@cache` without a positive `ttl` no longer stores responses or answers
  304 from a stored ETag.** With `ttl=None` or `ttl=0`, the response was stored
  without expiry, and a request whose `If-None-Match` matched the stored ETag
  got a 304 without the handler running. After the data changed, a client
  revalidating with the old ETag kept getting 304 until another request
  rewrote the entry, and entries for every query string accumulated. These
  routes now skip the backend like `private=True` ones: the handler runs on
  every request, and a 304 is sent only when `If-None-Match` matches the
  freshly rendered response. Entries that earlier versions stored for them
  are no longer read; `clear()` removes them.
  ([#110](https://github.com/allen0099/FastAPI-CacheX/issues/110))

- **The session middleware reads `Authorization: bearer <token>` in any
  letter case.** Authentication scheme names are case-insensitive (RFC 9110
  §11.1), but only the exact `Bearer ` prefix was recognised, so a client
  sending `bearer` got no session. More than one space before the token is
  accepted too, and a header without a token no longer yields an empty one.
  ([#166](https://github.com/allen0099/FastAPI-CacheX/issues/166))

- **Memcached `clear_path()` no longer reports a connection failure as
  "nothing to clear".** It caught every exception and returned `0`, while
  `delete()` and the other methods let the error through.
  ([#177](https://github.com/allen0099/FastAPI-CacheX/issues/177))

- **`MemoryBackend` rejects a `cleanup_interval` that is not positive.** With
  `0` or a negative value, `asyncio.sleep()` returned at once and the cleanup
  loop spun, using a full CPU core and taking the cache lock on every pass. It
  now raises `ValueError`.
  ([#180](https://github.com/allen0099/FastAPI-CacheX/issues/180))

- **Redis `get_all_keys()` no longer lists a key twice.** SCAN may return a
  key more than once when the keyspace shrinks during the iteration, and the
  duplicates were passed through to `get_all_keys()`, `CacheManager` and the
  monitoring routes. Scanned keys are now deduplicated.
  ([#173](https://github.com/allen0099/FastAPI-CacheX/issues/173))

- **`MemoryBackend` no longer lists or counts expired entries.** Entries
  that had expired but not yet been swept showed up in `get_all_keys()` and
  `get_cache_data()`, and `clear_pattern()`, `clear_path()` and
  `delete_many()` counted them as removed. Redis never returns an expired key,
  so `CacheManager.clear_prefix()` and the monitoring routes reported
  different numbers for the same live keys depending on the backend. The
  expired entries are still removed; they are just not reported. As on Redis,
  the monitoring routes' `expired_*` counts now stay at zero, apart from an
  entry that expires while the route runs.
  ([#178](https://github.com/allen0099/FastAPI-CacheX/issues/178))

- **`MemoryBackend.clear_pattern()` is case-sensitive on Windows.** It used
  `fnmatch.fnmatch`, which folds case through `os.path.normcase` on Windows, so
  `cache:user:*` also cleared `cache:User:1`. It now uses `fnmatch.fnmatchcase`,
  like Redis on every platform. The backends docs list where the glob syntax
  still differs from Redis.
  ([#179](https://github.com/allen0099/FastAPI-CacheX/issues/179))

- **Sessions no longer outlive `absolute_timeout`.** A sliding renewal, or a
  `session_ttl` longer than `absolute_timeout`, set `expires_at`, and with it
  the backend TTL and the JWT `exp`, past `created_at + absolute_timeout`. The
  expiry is now capped there, and a session whose expiry already sits at the
  cap is not renewed again, so it does not get a new token on every request.
  Because the backend now drops the record at the cap, a token presented
  after it raises `SessionNotFoundError`, as after an ordinary `session_ttl`
  expiry, instead of `SessionExpiredError`.
  ([#164](https://github.com/allen0099/FastAPI-CacheX/issues/164))
- **`MemoryBackend` restarts its cleanup task on a new event loop.** The task
  stayed tied to the loop of the first cache call. If that loop was closed
  without cancelling it, a backend reused on another loop never cleaned up
  again. The task is now started again on the current loop, and a task left
  on a loop that is still open is cancelled there.
  ([#181](https://github.com/allen0099/FastAPI-CacheX/issues/181))
- **`MemcachedBackend` raises while a server is unreachable instead of
  returning made-up results.** pymemcache's default retries answered calls in
  the second after a failure with each command's default, so `get()` looked
  like a miss, `set()` dropped the write, `increment()` returned 0 and
  `delete_if_equals()` raised `TypeError`. A failed server is now taken out of
  rotation at once and tried again after one second, instead of after 60.
  ([#197](https://github.com/allen0099/FastAPI-CacheX/issues/197))
- **`SessionConfig` warns when `cookie_same_site="none"` is set without
  `cookie_https_only=True`.** Browsers reject a `SameSite=None` cookie that is
  not `Secure`, so the session cookie was silently never stored. The
  combination is still accepted.
  ([#167](https://github.com/allen0099/FastAPI-CacheX/issues/167))
- **Memcached `get_and_delete()` uses CAS deletion to avoid deleting concurrent
  writes.**
  The get-then-delete sequence allowed a concurrent writer to update the key
  between the two calls, causing `get_and_delete()` to delete the new value
  while returning the old one. It now issues `gets` and a `cas` write with
  `exptime=-1`. If the key is updated before `cas` runs, the operation retries
  to retrieve and remove the current value, matching Redis `GETDEL`, and raises
  `CacheXError` if retries run out.
  ([#175](https://github.com/allen0099/FastAPI-CacheX/issues/175))
- **`clear_expired_sessions()` also removes invalidated and expired-status
  sessions.** It only checked `expires_at`, so a session marked `INVALIDATED`
  by `invalidate_session()` or `EXPIRED` by an expired read stayed in the
  backend until its TTL ran out. Any session that is no longer `ACTIVE` is now
  removed. `clear_expired_sessions()` and `delete_user_sessions()` also delete
  what they find with one `backend.delete_many()` call instead of one `delete`
  per session.
  ([#165](https://github.com/allen0099/FastAPI-CacheX/issues/165))
- **The session middleware varies on the header that carried the token.**
  `FastAPICacheXSessionMiddleware` added `Vary: Cookie` whenever
  `request.session` was accessed, even when the token came in
  `X-Session-Token` or `Authorization`, so a shared cache could key those
  responses on the wrong header. It now varies on every request header it
  read to find the token, and on `Cookie` only when no header carried one.
  ([#168](https://github.com/allen0099/FastAPI-CacheX/issues/168))
- **The wheel and sdist ship the LICENSE file.** `pyproject.toml` declared
  `Apache-2.0` but no `license-files`, so neither artifact contained the
  licence text the Apache-2.0 licence requires recipients to receive. The
  package also carries the `Typing :: Typed` classifier now.
  ([#232](https://github.com/allen0099/FastAPI-CacheX/issues/232))

### Security

- **`rotate_session_id(request)` gives the request's session a new ID at
  login.** With `FastAPICacheXSessionMiddleware`, a Starlette-style login that
  only writes to `request.session` keeps the session ID the request arrived
  with, so whoever planted that cookie was logged in too. Call it before
  attaching the user; with no session loaded it does nothing, since the first
  write starts a fresh one. The SESSION.md example used `SessionDep` and
  answered `401` to new visitors; it now uses the helper, and the migration
  section warns about the difference from Starlette.
  ([#225](https://github.com/allen0099/FastAPI-CacheX/issues/225))
- **OAuth states can be bound to the browser that started the flow.**
  `create_state(binding=...)` stores the SHA-256 of a client secret, such as a
  nonce also set as a cookie, and `consume_state(state, binding=...)` rejects
  the state with `InvalidStateError` unless the same value is given. Without a
  binding any stored state completes the flow in any browser, which allowed
  login CSRF although STATE.md described the states as CSRF protection. The
  quick start now sets and checks a binding cookie.
  ([#226](https://github.com/allen0099/FastAPI-CacheX/issues/226))
- **`request.session.clear()` logs out a session whose data was empty.** With
  `FastAPICacheXSessionMiddleware`, whether the data started out empty decided
  what a cleared session meant, so a user session created without data
  survived `clear()` and stayed logged in. `clear()` on a loaded session now
  always deletes it; keys written after `clear()` go into a new anonymous
  session. The same rule logged a user out when the last key was removed with
  `del` or `pop()`, e.g. a flash message; such a session is now saved with
  empty data. An emptied anonymous session is still deleted.
  ([#227](https://github.com/allen0099/FastAPI-CacheX/issues/227))
- **A `ttl` must be an `int` up to `MAX_TTL`, and `delta` an `int` in 64-bit
  range.** On Redis, `increment(key, ttl=1.5)` created the counter and then
  failed at `EXPIRE`, leaving a counter that never expired (a permanent
  lockout for a rate limiter) behind an error saying the key was "not a
  counter". `validate_ttl` now raises `TypeError` for `float`, `bool` and
  other types, and `ValueError` above `MAX_TTL` (2**31 - 1 seconds), before
  any backend I/O. A float TTL used to work on the memory backend only.
  `increment` checks `delta` the same way. Memcached now raises `ValueError`
  for a `ttl` whose expiry falls after 2038-01-19, which it used to accept and
  then drop at once, and reports only non-numeric values as "not a counter".
  `@cache` rejects such a `ttl` when the decorator is applied.
  ([#229](https://github.com/allen0099/FastAPI-CacheX/issues/229))
- **A `|||` in the `Host` header or path can no longer poison another
  path's cache entry.** The default key builder joined the raw host and
  decoded path with `|||`, so `GET /x` with `Host: example.com|||/p` stored
  its response under the key of `GET /p%7C%7C%7C/x`. `|` and `%` in the host
  and path are now percent-encoded (`escape_key_component` in
  `fastapi_cachex.types`); `clear_path()` encodes its argument the same way
  and the monitoring routes decode for display. Keys whose host or path
  contains `|` or `%` change, so those entries are cached afresh once. The
  HTTP caching guide now recommends `TrustedHostMiddleware`.
  ([#230](https://github.com/allen0099/FastAPI-CacheX/issues/230))
- **Releases run from master only, and only the publish step can reach
  PyPI.** `release.yml` could be dispatched on any branch, pushing the
  release commit there and publishing unmerged code, and the one job that
  installed every dev dependency also held `contents: write` and
  `id-token: write`. A non-dry-run dispatch off `master` now fails at once.
  The workflow is split into a read-only `build` job, a `release` job that
  commits, tags and creates the GitHub release, and a `publish` job in the
  `pypi` environment that alone can mint the PyPI token.
  ([#231](https://github.com/allen0099/FastAPI-CacheX/issues/231))
- **The JWT serializer warns about an HMAC key shorter than RFC 7518
  requires.** `secret_key` needs only 32 characters, but `HS384` and `HS512`
  need 48 and 64 bytes. `JWTTokenSerializer` now emits one `UserWarning`
  when it is built with a shorter key, instead of relying on PyJWT's
  per-token `InsecureKeyLengthWarning`.
  ([#116](https://github.com/allen0099/FastAPI-CacheX/issues/116))

### Documentation

- **Runnable examples.** `examples/` holds one complete FastAPI app per
  feature: HTTP caching, `CacheManager`, cookie and JWT sessions, OAuth
  state, `CacheLock`, a rate limiter on `increment()` and a Redis backend.
  `tests/test_examples.py` runs each one's main flow, so they keep working
  as the library changes. The guide pages link to the matching example.
  ([#241](https://github.com/allen0099/FastAPI-CacheX/issues/241))
- **Guides checked against 0.3.8.** The distributed lock and contributing
  guides are now available in Traditional Chinese. The JWT claims guide uses
  `FastAPICacheXSessionMiddleware`, and its custom serializer no longer reads
  private attributes. Corrected statements: `invalidate()` raises backend
  errors, only the Redis and Memcached backends prefix their keys, and
  `CacheLock` is a lease rather than a guarantee of mutual exclusion.
  ([#214](https://github.com/allen0099/FastAPI-CacheX/issues/214))
- **Logging a user in.** The session guide now shows how to attach a
  `SessionUser` at login so `require_user_session` and
  `AuthenticatedSession` accept the session; writing to `request.session`
  alone does not. ([#294](https://github.com/allen0099/FastAPI-CacheX/pull/294))

## [0.3.7] - 2026-09-25

### Added

- `CacheManager.add(key, value, ttl=None) -> bool` stores an application value
  only when the key is free and reports whether it did. It uses the same key
  prefix, JSON encoding and `default_ttl` as `set()`, and runs on the
  backend's atomic `set_if_absent`, so of several concurrent callers exactly
  one wins — for "send this webhook once" style deduplication. ([#65](https://github.com/allen0099/FastAPI-CacheX/issues/65))
- `add_routes(..., include_content_preview=False)` leaves response bodies out
  of `/cached-records`: `content_preview` is `null`, while keys, sizes and expiry
  are still reported. The default stays `True`. ([#79](https://github.com/allen0099/FastAPI-CacheX/issues/79))
- `get_client_ip(request, config)` (exported from `fastapi_cachex.session`) and
  the `ClientIPDep` dependency (`fastapi_cachex.session.dependencies`) return
  the client address the session middleware checks `ip_binding` against,
  honouring `trusted_proxies`. Pass it to `create_session()`: behind a trusted
  proxy, `request.client.host` is the proxy's address, so a session bound to it
  was rejected on its next request. ([#87](https://github.com/allen0099/FastAPI-CacheX/issues/87))
- `SessionConfig.trusted_proxies` accepts CIDR ranges (`10.0.0.0/8`,
  `2001:db8::/32`) as well as single addresses, for load balancers that connect
  from a subnet. It applies to both the peer check and the `X-Forwarded-For`
  walk. IPv4-mapped IPv6 peers match IPv4 entries, and non-IP entries such as
  `testclient` still match exactly. An entry containing `/` that is not a valid
  range now fails config validation. ([#73](https://github.com/allen0099/FastAPI-CacheX/issues/73))

### Fixed

- The Cache-Control table in the HTTP caching guide no longer marks
  header-only directives as simply "supported". It now shows, for each
  directive, how to set it, whether it is sent, and what it does to the
  server-side cache. A new section documents that the request's own
  `Cache-Control` is ignored by design.

- Docstrings and guides that disagreed with the code are corrected. Most
  visible:
  - `SessionConfig.sliding_threshold` now describes renewal once less than
    that fraction of the TTL remains; it used to say the opposite.
  - The `cache()` arguments `no_cache`, `stale_ttl`, `private` and `ttl` are
    described by what they do, and the docstring gains a `Raises:` section.
  - The per-user `key_builder` example in the HTTP caching guide no longer
    sets `private=True`, which bypassed the backend and made the key builder
    unused.
  - The state quick start catches `StateError`, so a malformed state is a 400
    instead of a 500.
  - The `get_session_manager` 500 message and the session dependency
    docstrings name `FastAPICacheXSessionMiddleware` instead of the
    deprecated `SessionMiddleware`.
  - The monitoring routes no longer claim to count cache hits.

- The Redis backend now matches its key prefix and the path given to
  `clear_path()` literally when it builds `SCAN` patterns. Glob characters in
  them used to be live: `clear_path("/files/[draft]")` missed the cached entry
  for that path, and a `key_prefix` containing `?` or `*` let `clear()`,
  `get_all_keys()` and `clear_pattern()` reach keys under other prefixes. Only
  the pattern passed to `clear_pattern()` is still a glob.

- `StateManager` no longer writes the raw OAuth state to its logs. The state
  comes from the callback query string, so logging it leaked live tokens and let
  a caller forge log lines with CR/LF. Log lines now carry `state_ref`, the
  first 12 hex characters of the state's SHA-256. An unknown or expired state
  in `consume_state()` is logged at INFO instead of WARNING, and malformed
  stored data is logged once at WARNING without a traceback, instead of two
  ERROR records with a traceback that echoed the stored state.

- Regenerating the session ID of the request's session (the documented defence
  against session fixation at login) now sends a token for the new ID.
  `FastAPICacheXSessionMiddleware` used to re-send the loaded token, which named
  the record `regenerate_session_id()` had just deleted, so the user was logged
  straight back out. Header clients got no token at all. The deprecated
  `SessionMiddleware` could overwrite the new token with a sliding-renewed one
  for the old ID. Both middlewares now notice the changed ID and send the new
  token through the request's transport. `SessionManager.issue_token(session)`
  is the one place tokens are signed.
  ([#103](https://github.com/allen0099/FastAPI-CacheX/issues/103))

- `ttl` means the same thing on every backend. Zero or negative TTLs now raise
  `ValueError` from `set`, `set_if_absent` and `increment` on all built-in
  backends, from the base-class fallbacks, and from `CacheManager` and
  `StateManager` (defaults included). Before, Memcached stored the entry
  forever, Redis failed with `invalid expire time`, and the memory backend
  expired it at once. `None` remains the way to say "no expiry", and
  `validate_ttl()` in `fastapi_cachex.backends.base` lets third-party backends
  apply the same rule. `@cache(ttl=0)` stays valid: it sends `max-age=0` and
  keeps the entry only for ETag revalidation, like `ttl=None`, instead of
  answering 500 on Redis or replaying the first response forever on
  Memcached. A negative `@cache` ttl raises `CacheXError` at decoration time.
  ([#102](https://github.com/allen0099/FastAPI-CacheX/issues/102))

- A `@cache` handler that returns plain data instead of a `Response` is
  rendered the way FastAPI renders it. The result goes through the route's
  response model (validation, field filtering and the `response_model_*`
  options) or `jsonable_encoder`, so a Pydantic model, `datetime` or `UUID` no
  longer fails to encode. The route's `status_code` applies (a `204` drops the
  body), and the status and headers set on an injected `response: Response`
  are kept, on cache hits as well.
  ([#99](https://github.com/allen0099/FastAPI-CacheX/issues/99))

- A sync (`def`) handler under `@cache` runs in the threadpool again. The
  cache wrapper is `async`, so FastAPI stopped offloading the handler and
  `@cache` called it on the event loop, where blocking I/O stalled every other
  request. A handler whose call returns an awaitable (an object with an
  `async def __call__`, or a sync callable returning a coroutine) is now
  awaited instead of failing to encode.
  ([#100](https://github.com/allen0099/FastAPI-CacheX/issues/100))

- `CacheManager.get_or_set()` awaits whatever the factory returns when it is
  awaitable. The documented `get_or_set(key, lambda: load_user(42))` form used
  to store the coroutine itself and fail with `TypeError`. ([#101](https://github.com/allen0099/FastAPI-CacheX/issues/101))

- The monitoring routes from `add_routes()` now show when Redis entries
  expire. `AsyncRedisCacheBackend.get_cache_data()` reported every entry as
  never expiring (`ttl_remaining: null`); it now fetches each key's `PTTL` in
  the same pipeline as its value and returns the absolute expiry the memory
  backend reports. A key that disappears between the scan and the fetch is left
  out. ([#74](https://github.com/allen0099/FastAPI-CacheX/issues/74))
- The client IP used for `ip_binding` now walks every `X-Forwarded-For` header
  line, not only the first. A proxy that adds its own line instead of appending
  to the caller's left a caller-chosen first line in charge of the walk, so a
  forged address could satisfy the binding. ([#104](https://github.com/allen0099/FastAPI-CacheX/issues/104))

### Documentation

- The guides are available in Traditional Chinese at
  <https://fastapi-cachex.readthedocs.io/zh-tw/latest/>, with a language
  switcher on both sites. English stays the source of truth: every translated
  page says it may lag behind and links to its English original. The API
  reference and the development guides remain English-only.
  `docs/README.zh-TW.md` is replaced by the translated home page. ([#89](https://github.com/allen0099/FastAPI-CacheX/issues/89))

## [0.3.6] - 2026-09-25

### Added

- `BaseCacheBackend.set_if_absent(key, value, ttl=None) -> bool` and
  `delete_if_equals(key, expected) -> bool`, the atomic pair for locks and
  per-user slots: claim a key only when it is free, and release it only while
  it still holds your entry, so a holder whose entry expired cannot free a slot
  someone else has claimed since. Redis uses `SET NX EX` and a Lua
  compare-and-delete, Memcached `ADD` and `GETS` + `CAS`, memory its lock.
  Third-party backends inherit non-atomic fallbacks. ([#62](https://github.com/allen0099/FastAPI-CacheX/issues/62))

### Fixed

- Concurrent first requests to the `AppCache` dependency, in an app that never
  configured a backend, no longer each build their own `MemoryBackend` and
  `CacheManager`. `get_app_cache` runs in worker threads, so the last one
  registered replaced the others and, for a while, requests used caches that
  could not see each other's entries. The lazy set-up and the `@cache`
  fallback now happen under one lock. ([#76](https://github.com/allen0099/FastAPI-CacheX/issues/76))
- A JWT session configured with an asymmetric `jwt_algorithm` (`RS*`, `ES*`,
  `PS*`, `EdDSA`) and the built-in serializer now fails when the
  `SessionManager` is built, with a `ValueError` that names the fix. The
  built-in serializer only has the `secret_key` string, so such a configuration
  was accepted at startup and then failed inside PyJWT on the first
  `create_session()`. Only the HMAC algorithms work without a custom
  `token_serializer`; configurations that pass one are unaffected. ([#86](https://github.com/allen0099/FastAPI-CacheX/issues/86))

### Documentation

- The documentation is published at <https://fastapi-cachex.readthedocs.io/>,
  built with Zensical and including an API reference generated from the
  docstrings. The package metadata links to it as `Documentation`.
- Every guide is now in English and was checked against the code. Among the
  corrections: `docs/JWT_CLAIMS.md` told you to install a custom token
  serializer by assigning `manager._token_serializer`, which has no effect —
  pass `token_serializer=` to `SessionManager` instead; and the cache-flow
  guide said `clear()` is a no-op on Memcached, when it runs `flush_all` and
  empties the whole server.
- `README.md` is now a short landing page. Its reference material moved to new
  guides: HTTP caching, Application cache and Backends.
- `StateManager.create_state()` no longer documents `StateDataError` for backend
  failures: backend errors propagate unchanged. ([#88](https://github.com/allen0099/FastAPI-CacheX/issues/88))

### Testing

- The changelog test takes the previous version from the latest released
  heading instead of naming it, so it no longer has to be edited after every
  release.

## [0.3.5] - 2026-09-15

### Security

- `SessionMiddleware` no longer trusts `X-Forwarded-For`/`X-Real-IP` by default.
  Forwarded addresses are only honoured when the peer is listed in the new
  `SessionConfig.trusted_proxies`, and the address taken is the rightmost entry
  that is *not* a trusted proxy — the leftmost entry is attacker-controlled.
  **If you run behind a reverse proxy and rely on `bind_ip`, you must now set
  `trusted_proxies`**, otherwise IP binding sees the proxy's address.
  `trusted_proxies` matches exact strings; CIDR ranges are not supported yet.
- JWT session tokens reject unsafe algorithms (`none` and asymmetric algorithms
  fed a symmetric key) instead of accepting them.
- `private=True` responses are no longer written to or read from the shared
  backend. Previously a response marked private was still stored where every
  other caller could read it; only `If-None-Match` revalidation is kept.
- **Check your key builder if you copied the per-user caching example from the
  README of 0.3.4 or earlier.** That example built the cache key from an
  unverified `X-User-Id` request header, immediately above a paragraph warning
  against exactly that. Code copied from it is a horizontal privilege
  escalation: sending `X-User-Id: <someone-else>` returns that user's cached
  response. The example now reads an identity the authentication layer verified
  and wrote to `request.state`, and the warning is a CAUTION block showing the
  header version as an explicit anti-example.

### Added

- `fastapi_cachex.__version__`, read from the installed distribution metadata.
- `docs/STATE.md`: documentation for the previously undocumented state
  subsystem (`StateManager`, one-shot `consume_state()`, the dependency
  injection helpers).

### Deprecated

- `SessionMiddleware` is now scheduled for removal in 0.4.0 rather than 0.3.5.
  Nothing about the class changes — it has emitted a `DeprecationWarning` since
  0.3.1 and still does — but 0.3.5 is a patch release, and removing an exported
  public class in a patch release is a breaking change no matter how small the
  migration is. The runtime warning, the docstring and the guides all name
  0.4.0 now, which is where `delete()` returning `bool` and the removal of
  `BackendProxy.get_backend()`/`set_backend()` were already scheduled.

### Removed

- The `starlette` extra. All it ever pulled in was `itsdangerous`, which is a
  base dependency now, so the extra adds nothing. Installing
  `fastapi-cachex[starlette]` still resolves — an unknown extra is a warning,
  not an error — it simply has no effect.

### Changed

- `itsdangerous` is a required dependency rather than an extra, so
  `FastAPICacheXSessionMiddleware` works on a plain `pip install fastapi-cachex`.
  It reuses `starlette.middleware.sessions.Session` for its dict-like
  `scope["session"]`, and that module imports `itsdangerous` at module level, so
  the middleware could never be constructed without it — the extra only moved
  the failure from install time to runtime.
- A cache hit now replays the status code and the headers the handler produced,
  instead of always returning `200` with no headers. Entries written by earlier
  versions are still readable and replay as `200`.
- `If-None-Match` follows RFC 9110 §8.8.3.2: weak comparison, `*`, and
  multi-value headers all match correctly. A `304` now carries the
  `Cache-Control`, `Content-Location`, `Expires` and `Vary` headers the `200`
  would have carried (§15.4.5), rather than only `ETag` and `Cache-Control`.
- `clear_pattern()` globs the whole cache key on every backend. Previously each
  backend interpreted the pattern differently; a pattern shaped like a bare path
  now clears nothing and emits a `RuntimeWarning` saying so, instead of silently
  matching nothing.
- Memcached keys longer than the protocol's 250-byte limit, or containing bytes
  it refuses, are stored under a SHA-256 digest instead of failing. TTLs beyond
  30 days are sent as an absolute timestamp, as the protocol requires — they
  previously expired immediately.
- The `MemoryBackend` sweeper starts on writes as well as reads, so a
  write-only workload (for example `StateManager.create_state`) no longer
  accumulates expired entries.
- `@cache` handlers that take `**kwargs`, or that annotate `Request` as a
  string, no longer crash when the decorator injects the request parameter.

### Fixed

- `AppCache` falls back to a `MemoryBackend` when no backend is configured,
  matching what `@cache` already did.

### Documentation

- `docs/CACHE_FLOW.md` corrected against the code: the decorator parameter is
  `ttl` (not `max_age`), the cache key separator is `|||` (not `:`), network
  backends store latin-1 round-tripped JSON (not base64), and the entry
  structure matches the current `CacheEntry`/`CacheItem` dataclasses.
- `README.md` documents `invalidate()`, `add_routes()`, `CacheManager.get_or_set()`
  / `clear_pattern()`, `RedisConfig.load_from_config()` and the extras. It now
  warns that the monitoring endpoints have **no authentication** and that
  `/cached-records` previews cached content, and notes that the Redis backend
  reports every entry as never expiring because `get_cache_data()` returns no
  TTL.
- `docs/SESSION.md` states that `SessionMiddleware` is deprecated since 0.3.1
  and will be removed in 0.4.0, and that cookie transport is provided only by
  `FastAPICacheXSessionMiddleware`.

### Testing

- The Redis and Memcached suites are now opt-in: they wipe the server they
  connect to, so they skip unless `CACHEX_TEST_REDIS_PORT` /
  `CACHEX_TEST_MEMCACHED_PORT` names one. Contributors must point them at a
  throwaway server; see `docs/DEVELOPMENT.md`.
- `CACHEX_REQUIRE_LIVE_SERVERS=1`, set by every CI workflow, makes a skipped
  live-server suite fail the run. Opting in kept a stray `pytest` from wiping a
  developer's data, but it also meant a mistyped port silently dropped those
  suites while coverage stayed near 97% and the job went green.

### Release process

- Releases are cut by one workflow instead of two. `publish.yml` (patch) and
  `release.yml` (minor) were the same steps twice over, so which part of the
  version a release moved depended on which Actions page was opened; `Release`
  now takes the bump as an input, with an exact version as an override.
- The GitHub release notes are this file's `## [Unreleased]` section, promoted
  by `scripts/changelog_release.py`, rather than a list of commit subjects. A
  release with an empty `## [Unreleased]` stops instead of publishing notes
  that say nothing; the commit list is still reachable through the compare
  link at the end of the notes.
- The release runs the full test suite — including the Redis and Memcached
  suites, which cannot skip there — before it writes, tags or publishes
  anything, and refuses a version that is already tagged.
- The release can be rehearsed: dispatching it with `dry_run` runs the gate,
  the version bump, the changelog promotion and the build, then stops short of
  the four steps that commit, tag, release and publish. The release notes and
  the built distributions are attached to the run so they can be inspected
  before the real thing.

## [0.3.4] - 2026-09-05

### Added

- Atomic backend primitives on `BaseCacheBackend`, overridden by every built-in
  backend: `increment()` (Redis Lua script, Memcached `ADD` + `INCR`) and
  `get_and_delete()` (Redis `GETDEL`, Memcached get + acknowledged delete).
  `StateManager.consume_state()`, `delete_state()`, `CacheManager.delete()` and
  `invalidate()` use them, making one-shot retrieval genuinely atomic.
- `delete_many()`, batched into a single `DEL` on Redis and a single lock
  acquisition on Memory; `clear_prefix()` is built on it.

### Fixed

- The Memcached client pools connections and waits for write acknowledgements,
  so concurrent calls no longer share a socket and a write is visible to the
  next read.

## [0.3.2] - 2026-07-29

### Fixed

- Cached responses use the application's configured default response class
  instead of a hard-coded one.

## [0.3.1] - 2026-07-07

### Added

- `FastAPICacheXSessionMiddleware`: backend-backed session middleware that
  supports cookie transport as well as the `X-Session-Token` header and
  `Authorization: Bearer`, and routes the response side by token source.
- `CacheManager.get_or_set()` and `CacheManager.clear_pattern()`.
- `invalidate()` for busting a single cached route.
- `StateManagerProxy` and the `get_state_manager` dependency.

### Deprecated

- `SessionMiddleware`, in favour of `FastAPICacheXSessionMiddleware`. It emits a
  `DeprecationWarning` at construction and will be removed in 0.3.5.

### Fixed

- `MemoryBackend.clear_pattern()` no longer misses keys without a separator.
- `SessionConfig` rejects unknown fields instead of silently ignoring them.

## [0.3.0] - 2026-07-02

Baseline for this changelog. Earlier releases are described in the
[GitHub releases](https://github.com/allen0099/FastAPI-CacheX/releases).

[Unreleased]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.9...v0.4.0
[0.3.9]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.8...v0.3.9
[0.3.8]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.7...v0.3.8
[0.3.7]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.2...v0.3.4
[0.3.2]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/allen0099/FastAPI-CacheX/releases/tag/v0.3.0
