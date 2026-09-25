# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file records what changed for users of the library, in particular
behaviour that changed under an unchanged API. The `## [Unreleased]` section is
what the Release workflow publishes as the GitHub release notes, and a release
with an empty one fails — so entries are added by hand, in the pull request
that earns them. See [Releasing](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/#releasing).

Note that 0.3.3 was never released; 0.3.4 follows 0.3.2.

## [Unreleased]

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

- A sync (`def`) handler under `@cache` runs in the threadpool again. The
  cache wrapper is `async`, so FastAPI stopped offloading the handler and
  `@cache` called it on the event loop, where blocking I/O stalled every other
  request. A handler whose call returns an awaitable (an object with an
  `async def __call__`, or a sync callable returning a coroutine) is now
  awaited instead of failing to encode.
  ([#100](https://github.com/allen0099/FastAPI-CacheX/issues/100))

- The monitoring routes from `add_routes()` now show when Redis entries
  expire. `AsyncRedisCacheBackend.get_cache_data()` reported every entry as
  never expiring (`ttl_remaining: null`); it now fetches each key's `PTTL` in
  the same pipeline as its value and returns the absolute expiry the memory
  backend reports. A key that disappears between the scan and the fetch is left
  out. ([#74](https://github.com/allen0099/FastAPI-CacheX/issues/74))

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

[Unreleased]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.6...HEAD
[0.3.6]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.2...v0.3.4
[0.3.2]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/allen0099/FastAPI-CacheX/releases/tag/v0.3.0
