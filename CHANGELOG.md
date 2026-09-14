# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

GitHub release notes are generated from commit subjects; this file records what
changed for users of the library, in particular behaviour that changed under an
unchanged API. It is maintained by hand — see
[Releasing](docs/DEVELOPMENT.md#releasing) for what to do at release time.

Note that 0.3.3 was never released; 0.3.4 follows 0.3.2.

## [Unreleased]

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

### Removed

- The `starlette` extra. `itsdangerous` is a base dependency now, so
  `FastAPICacheXSessionMiddleware` works on a plain `pip install fastapi-cachex`.
  It reuses `starlette.middleware.sessions.Session` for its dict-like
  `scope["session"]`, and that module imports `itsdangerous` at module level, so
  the middleware could never be constructed without it — the extra only moved
  the failure to runtime. Installing `fastapi-cachex[starlette]` still resolves
  (an unknown extra is a warning, not an error), it simply adds nothing.

### Changed

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
  and will be removed in 0.3.5, and that cookie transport is provided only by
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

[Unreleased]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.4...HEAD
[0.3.4]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.2...v0.3.4
[0.3.2]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/allen0099/FastAPI-CacheX/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/allen0099/FastAPI-CacheX/releases/tag/v0.3.0
