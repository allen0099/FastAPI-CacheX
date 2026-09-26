# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file records what changed for users of the library, in particular
behaviour that changed under an unchanged API. Entries are added by hand, in
the pull request that earns them, and each one opens with a bold one-line
summary: `- **What changed.** The details...`. The GitHub release notes list
those summaries and link back here, and a release fails if `## [Unreleased]`
is empty or an entry has no summary. Entries before 0.3.8 predate this format.
See [Releasing](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#releasing).

Note that 0.3.3 was never released; 0.3.4 follows 0.3.2.

## [Unreleased]

### Added

- **`CacheLock`, a distributed lock built on backend primitives.** Usable as an async context manager or with direct `acquire`/`release`/`extend`/`locked` calls, raising `LockTimeoutError` on timeout. ([#64](https://github.com/allen0099/FastAPI-CacheX/issues/64))
- **`expire_if_equals()` backend primitive for owner-checked TTL renewal.** Added to `BaseCacheBackend`, `MemoryBackend`, `AsyncRedisCacheBackend`, and `MemcachedBackend`. ([#64](https://github.com/allen0099/FastAPI-CacheX/issues/64))

### Changed

- **GitHub release notes list one line per change.** Each changelog entry now
  opens with a bold one-line summary. The release page shows only those
  summaries with their issue links, grouped as in the changelog, and links to
  the full entries on the documentation site. The changelog itself keeps the
  details.

### Deprecated

- **Passing the Redis key prefix in a `clear_pattern()` pattern.** When such a
  pattern clears nothing, the prefix-stripped form is still tried and emits a
  `DeprecationWarning` if it clears anything. The retry will be removed in
  0.4.0. ([#125](https://github.com/allen0099/FastAPI-CacheX/issues/125))

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

[Unreleased]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.7...HEAD
[0.3.7]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.2...v0.3.4
[0.3.2]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/allen0099/FastAPI-CacheX/releases/tag/v0.3.0
