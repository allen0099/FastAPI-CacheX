# Migrating to 0.4.0 {#migrating-to-040}

0.3.9 is the last 0.3.x release. 0.4.0 contains breaking changes, collected in the [0.4.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.4.0). This page lists every one of them, what to change, and whether 0.3.9 already warns about it. Three changes 0.3.9 announced for sessions are not made as announced; see [Token sources](#token-source-priority) and [get_session_manager](#get-session-manager).

## Before you upgrade {#before-you-upgrade}

Upgrade to 0.3.9 first and run your test suite with the library's warnings turned into errors:

```bash
python -W error::DeprecationWarning -W error::FutureWarning -m pytest
```

or, with pytest's own setting:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error::DeprecationWarning",
    "error::FutureWarning",
]
```

Every warning below names the setting to change and links to its issue. `FutureWarning` announces a default that changes behaviour and is shown by default; `DeprecationWarning` announces a removal; `UserWarning` flags a configuration that is already wrong today and that 0.4.0 no longer accepts. Once 0.3.9 runs without these warnings, the changes with a warning in the "Warned in 0.3.9" column are done (the "Dropped" rows need nothing); the rest of this page covers what no warning can detect.

## Summary {#summary}

| Change | Issue | Warned in 0.3.9 | Section |
|--------|-------|-----------------|---------|
| Sessions and OAuth state deprecated, removed in 0.5.0 | [#420](https://github.com/allen0099/FastAPI-CacheX/issues/420) | No (0.4.0 warns: `FutureWarning`) | [Sessions and OAuth state are deprecated](#session-state-deprecated) |
| Session cookie defaults to `__Host-session` with `Secure` | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | `FutureWarning` | [Session cookie](#session-cookie) |
| Contradictory `__Host-` / `__Secure-` cookie settings are rejected | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | `UserWarning` (`FutureWarning` when `cookie_name` is left at its default, under the middleware only) | [Session cookie](#session-cookie) |
| Explicit `login()` / `logout()`, read-only `Session.user` | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | No | [Login and logout](#login-logout) |
| `get_or_set()` locks by default | [#280](https://github.com/allen0099/FastAPI-CacheX/issues/280) | `FutureWarning` | [get_or_set lock](#get-or-set-lock) |
| Dropped: `get_session_manager` resolving through `SessionManagerProxy` | [#131](https://github.com/allen0099/FastAPI-CacheX/issues/131) | `FutureWarning` (0.4.0 no longer warns) | [get_session_manager](#get-session-manager) |
| Dropped: `token_source_priority` naming every token source | [#75](https://github.com/allen0099/FastAPI-CacheX/issues/75) | `FutureWarning` (0.4.0 no longer warns) | [Token sources](#token-source-priority) |
| `add_routes()` requires `dependencies`, no content preview by default | [#298](https://github.com/allen0099/FastAPI-CacheX/issues/298) | `UserWarning` (only when `dependencies` is left out) | [Monitoring routes](#add-routes) |
| Redis `encoding` option removed | [#126](https://github.com/allen0099/FastAPI-CacheX/issues/126) | `DeprecationWarning` (`RuntimeWarning` for a value other than UTF-8) | [Redis encoding](#redis-encoding) |
| JWT HMAC secrets shorter than the hash output are rejected | [#129](https://github.com/allen0099/FastAPI-CacheX/issues/129) | `UserWarning` | [JWT secret length](#jwt-secret) |
| `SessionMiddleware` removed | [#69](https://github.com/allen0099/FastAPI-CacheX/issues/69) | `DeprecationWarning` | [SessionMiddleware](#session-middleware) |
| `BackendProxy.get_backend()` / `set_backend()` removed | [#70](https://github.com/allen0099/FastAPI-CacheX/issues/70) | `DeprecationWarning` | [BackendProxy](#backend-proxy) |
| `CacheError` removed | [#130](https://github.com/allen0099/FastAPI-CacheX/issues/130) | `DeprecationWarning` | [CacheError](#cache-error) |
| Redis `clear_pattern()` prefix-stripped retry removed | [#125](https://github.com/allen0099/FastAPI-CacheX/issues/125) | `DeprecationWarning` | [Redis clear_pattern](#redis-clear-pattern) |
| `SessionConfig.use_bearer_token` stays deprecated, removed in 0.5.0 | [#377](https://github.com/allen0099/FastAPI-CacheX/issues/377), [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | `DeprecationWarning` | [Token sources](#token-source-priority) |
| `UserSessionDep` requires a user | [#127](https://github.com/allen0099/FastAPI-CacheX/issues/127) | No | [UserSessionDep](#user-session-dep) |
| `memcache` extra removed | [#202](https://github.com/allen0099/FastAPI-CacheX/issues/202) | No | [memcache extra](#memcache-extra) |
| `BaseCacheBackend.delete()` returns `bool` | [#71](https://github.com/allen0099/FastAPI-CacheX/issues/71) | No | [delete() return value](#backend-delete) |
| `CacheEntry.headers` becomes a tuple of pairs | [#105](https://github.com/allen0099/FastAPI-CacheX/issues/105) | No | [Repeated headers](#cache-entry-headers) |
| HTTP cache key format | [#271](https://github.com/allen0099/FastAPI-CacheX/issues/271), [#270](https://github.com/allen0099/FastAPI-CacheX/issues/270), [#269](https://github.com/allen0099/FastAPI-CacheX/issues/269), [#266](https://github.com/allen0099/FastAPI-CacheX/issues/266), [#265](https://github.com/allen0099/FastAPI-CacheX/issues/265), [#72](https://github.com/allen0099/FastAPI-CacheX/issues/72) | No | [Cache keys](#cache-keys) |
| Conditional session writes | [#128](https://github.com/allen0099/FastAPI-CacheX/issues/128) | No | [Session writes](#session-writes) |

## Sessions and OAuth state are deprecated {#session-state-deprecated}

`fastapi_cachex.session` and `fastapi_cachex.state` are deprecated in 0.4.0 and removed in 0.5.0 ([#420](https://github.com/allen0099/FastAPI-CacheX/issues/420), [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421)). FastAPI-CacheX is narrowing to HTTP and application caching. Session handling and OAuth state are security-sensitive, and libraries built for them maintain them better. 0.3.9 did not announce this, so both packages keep working throughout 0.4.x and get security fixes only.

Importing either package, or reading one of their names from `fastapi_cachex` (such as `fastapi_cachex.SessionConfig`), emits a `FutureWarning` that points at the importing line. `import fastapi_cachex` on its own does not warn, and neither do `@cache`, `CacheManager`, `CacheLock` or the backends. The session and state names are no longer in `fastapi_cachex.__all__`, so `from fastapi_cachex import *` stops providing them. Until you migrate, import them by name.

Where to move:

| You use | Move to |
|---------|---------|
| `FastAPICacheXSessionMiddleware` with cookie sessions | Starlette's `SessionMiddleware` keeps small session data in a signed cookie. If sessions must live on the server (large data, server-side revocation), use a server-side session library, for example `starsessions`. |
| Session tokens in a header or `Authorization: Bearer` for an API | The access tokens of your authentication stack, for example OAuth 2 bearer tokens verified with a JWT library. |
| `StateManager` for the OAuth/OIDC `state` | Your OAuth client library. Authlib's Starlette integration, for example, creates and checks `state` and `nonce` for you; it keeps them in `request.session`, so it needs Starlette's `SessionMiddleware`. |
| `CacheManager`, `CacheLock`, `@cache` | Nothing to do: they are not deprecated. |

Sessions and states already in the backend need no cleanup; they expire on their own TTL.

To silence the warning while you migrate, filter it before the first import:

```python
import warnings

warnings.filterwarnings(
    "ignore", message="fastapi_cachex.session is deprecated", category=FutureWarning
)
warnings.filterwarnings(
    "ignore", message="fastapi_cachex.state is deprecated", category=FutureWarning
)
```

## Sessions {#sessions}

These changes still apply if you keep using sessions during 0.4.x.

### Session cookie defaults {#session-cookie}

0.4.0 changes two `SessionConfig` defaults ([#256](https://github.com/allen0099/FastAPI-CacheX/issues/256)): `cookie_name` becomes `"__Host-session"` (from `"session"`) and `cookie_https_only` becomes `True` (from `False`). Browsers accept a `__Host-` cookie only when it is `Secure`, has `Path=/` and no `Domain`, and never from a subdomain, which removes the usual way to plant a session cookie. Two consequences:

- Every browser holding a `session` cookie is logged out once after the upgrade, because the middleware looks for `__Host-session` instead.
- A `Secure` cookie is not sent over plain HTTP, so local development without TLS needs an explicit opt-out.

In 0.3.9, `FastAPICacheXSessionMiddleware` emits a `FutureWarning` when its config leaves `cookie_name` or `cookie_https_only` at the default. Header-only setups (the deprecated `SessionMiddleware`, or `SessionManager` used without middleware) never send the cookie and do not warn.

Before:

```python
config = SessionConfig(secret_key=SECRET)
app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=SessionManager(backend, config)
)
```

After, keeping today's cookie (the same code works on 0.3.9 and 0.4.0, and is what plain-HTTP development needs):

```python
config = SessionConfig(
    secret_key=SECRET, cookie_name="session", cookie_https_only=False
)
```

After, switching now (HTTPS only):

```python
config = SessionConfig(
    secret_key=SECRET, cookie_name="__Host-session", cookie_https_only=True
)
```

0.4.0 also rejects contradictory settings: a `__Host-` name without `cookie_https_only=True`, with a `cookie_path` other than `"/"` or with a `cookie_domain`, and a `__Secure-` name without `cookie_https_only=True`. Browsers already refuse such a cookie, so the session never sticks; 0.3.9 emits a `UserWarning` when `SessionConfig` is built with one of them. The rejection is a `pydantic.ValidationError` (a `ValueError`) from `SessionConfig`. Because the default name is now `__Host-session`, a config that sets only `cookie_https_only=False`, a `cookie_path` or a `cookie_domain` and leaves `cookie_name` at its default raises too. 0.3.9 warned about that only under `FastAPICacheXSessionMiddleware`, with the `FutureWarning` above; a header-only setup that sets one of these options got no warning. Set `cookie_name` as well, as in the first After example, or remove the option if nothing reads the cookie.

### Login and logout {#login-logout}

0.4.0 makes becoming authenticated go through one explicit API that always issues a new session ID ([#256](https://github.com/allen0099/FastAPI-CacheX/issues/256)):

- `login(request, user)` (in 0.3.9 already, `from fastapi_cachex.session import login`) attaches the user and rotates the ID. Use it today instead of setting `session.user` yourself.
- `await logout(request)` (`from fastapi_cachex.session import logout`) deletes the session from the backend at once, so its token stops resolving before the response is sent, and a cookie client gets its cookie expired. It returns `False` when no session was loaded or started in the request. `request.session.clear()` keeps meaning logout.
- Assigning `session.user` raises `AttributeError`. The user is set by `login()` and `SessionManager.create_session(user=...)`, or given when a `Session` is built. Under the middleware, use `login()`; without it, create the session with `create_session(user=...)`.
- A login carries the anonymous session's data over by default, so a cart survives it. `login(request, user, keep=["cart"])` carries only the listed keys, and `keep=[]` carries nothing. A string is rejected with `TypeError`, since `keep="cart"` would otherwise mean its letters.
- The old ID stops resolving the moment `login()` or `rotate_session_id()` rotates it, with no grace period: during one, a planted token would resolve to the logged-in session.
- `rotate_session_id()` keeps its name, for privilege changes without a new user.

Most of this is new API, and nothing in 0.3.x can tell which code assigns `session.user`, so 0.3.9 does not warn. An application that decides "logged in" from its own `request.session` keys (`request.session.get("user_id")`) is outside what the library can see; use the library's identity (`session.user`, `AuthenticatedSession`) or rotate the ID yourself.

Before:

```python
session, _ = await manager.get_session(token)
session.user = SessionUser(user_id=user_id)  # read-only from 0.4.0
await manager.update_session(session)
```

After (works in 0.3.9, rotates the session ID too):

```python
from fastapi_cachex.session import login

await login(request, SessionUser(user_id=user_id))
```

### get_session_manager {#get-session-manager}

0.3.9 announced that 0.4.0 would resolve `get_session_manager` (and `SessionManagerDep`, `ClientIPDep` and `rotate_session_id()`, which use it) through `SessionManagerProxy` only ([#131](https://github.com/allen0099/FastAPI-CacheX/issues/131)), and emitted a `FutureWarning`. Since sessions are deprecated ([#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)), 0.4.0 does not make this change. It keeps returning the manager the middleware stored on `app.state` and no longer warns. If you already call `SessionManagerProxy.set()`, keeping it is harmless.

### UserSessionDep {#user-session-dep}

In 0.3.x, `UserSessionDep` is an alias of `SessionDep` and admits anonymous sessions. In 0.4.0 it requires a session with a user, like `AuthenticatedSession` ([#127](https://github.com/allen0099/FastAPI-CacheX/issues/127)), so anonymous requests to routes that use it get `401`. 0.3.9 does not warn: a type alias has no hook that runs when it is used, and a warning on import would fire for everyone. Pick the dependency that says what you mean:

```python
# Before
async def cart(session: UserSessionDep): ...


# After: anonymous sessions allowed (today's behaviour)
async def cart(session: SessionDep): ...


# After: a logged-in user required (0.4.0's UserSessionDep)
async def profile(session: AuthenticatedSession): ...
```

### SessionMiddleware {#session-middleware}

The header-only `SessionMiddleware` is removed ([#69](https://github.com/allen0099/FastAPI-CacheX/issues/69)); 0.3.x already emits a `DeprecationWarning`. Use `FastAPICacheXSessionMiddleware`, which also reads the header and `Authorization: Bearer` token and adds `request.session`. It also sends a session cookie to clients that sent no token, so set the cookie options as in [Session cookie](#session-cookie). See [Session management](SESSION.md#migration-sessionmiddleware-fastapicachexsessionmiddleware).

```python
# Before
app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

# After
app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=manager, config=config
)
```

### JWT secret length {#jwt-secret}

With `token_format="jwt"`, 0.4.0 raises `ValueError` at startup, when `SessionManager` builds its `JWTTokenSerializer`, if `jwt_algorithm` is `HS384` or `HS512` and `secret_key` is shorter than 48 or 64 bytes in UTF-8 (RFC 7518 section 3.2) ([#129](https://github.com/allen0099/FastAPI-CacheX/issues/129)). 0.3.x emitted a `UserWarning` there instead. Use a longer key, or `HS256`:

```python
# Before: 32 characters, too short for HS512
SessionConfig(
    secret_key=secrets.token_urlsafe(24), token_format="jwt", jwt_algorithm="HS512"
)

# After
SessionConfig(
    secret_key=secrets.token_urlsafe(64), token_format="jwt", jwt_algorithm="HS512"
)
```

### Session writes {#session-writes}

0.4.0 writes sessions conditionally ([#128](https://github.com/allen0099/FastAPI-CacheX/issues/128)): a request that loaded a session before another request deleted, invalidated or rotated it can no longer bring the record back when it saves. No code change is needed.

- Ordinary saves become conditional: the middleware's save of `request.session` changes, sliding renewal and `update_session()`. Each succeeds only while the stored record still equals what this request last read or wrote. Deleting, invalidating and expiring stay unconditional, so a security action always wins.
- A rejected save is dropped and logged. The response is still sent, without a session token. The exception is a renewal that this request already stored: while the session is still valid (the save lost only to another save), the renewed token is sent, so a JWT client does not keep a token that expires before the record.
- Rotating the ID (`regenerate_session_id()`) removes the old record atomically and goes ahead only if that record was still valid, so a copy read before another request deleted, invalidated or rotated the session cannot come back under a new ID. Otherwise it raises `SessionNotFoundError` or `SessionInvalidError` and stores nothing under a new ID (the old record is removed either way), and of two concurrent rotations of one session only the first succeeds. A change saved by another request in between does not stop the rotation. `rotate_session_id()` answers `401` in that case and sends no token, and `login()` starts a new session for the user instead, without the data of the ended one. A `Session` built by hand, never read from or written to the backend, is rotated as before.
- **Side effect:** when two requests change the same session at the same time (two tabs adding to a cart), the first save wins and the second is dropped. Today the last save wins, so one of the two changes is already lost; 0.4.0 changes which one. Merging such changes is tracked in [#376](https://github.com/allen0099/FastAPI-CacheX/issues/376).
- `update_session()` now returns `True` when the session was stored and `False` when the save was dropped. A `Session` built by hand, never read from the backend, is stored only if no record exists under its ID yet.
- When `get_session()` renews a session (sliding expiry) and the renewal loses a race with another save, it reads the session again and retries once. If it loses again, the request keeps the session it read and no renewed token is sent.
- `Session` equality ignores which backend entry each copy last read or wrote, so a loaded session still equals one built with the same fields.
- The backend gains `set_if_equals(key, expected, value, ttl=None)`, next to `delete_if_equals` and `expire_if_equals`. The base class provides a non-atomic fallback, so a custom backend keeps working unchanged; override it to make the save atomic.

### Token sources {#token-source-priority}

0.3.9 announced two changes here. Since sessions are deprecated ([#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)), 0.4.0 makes neither of them:

- **`token_source_priority` naming every token source ([#75](https://github.com/allen0099/FastAPI-CacheX/issues/75)) is dropped.** The middleware still reads the session cookie after the header sources whether or not the list names it, and `"cookie"` is still accepted only as the last entry. 0.4.0 no longer emits the `FutureWarning` for a list without `"cookie"`. A list that already ends in `"cookie"` keeps working.
- **`use_bearer_token` is not removed in 0.4.0 ([#377](https://github.com/allen0099/FastAPI-CacheX/issues/377)).** It stays deprecated, still emits a `DeprecationWarning` when it is passed, and is removed in 0.5.0 together with `fastapi_cachex.session`. `use_bearer_token=False` still turns bearer tokens off in 0.4.x; move to the list now, which alone decides from 0.5.0:

```python
# Before
SessionConfig(secret_key=SECRET, use_bearer_token=False)

# After
SessionConfig(secret_key=SECRET, token_source_priority=["header", "cookie"])
```

`use_bearer_token=True` is the default and can be dropped.

## Application cache {#application-cache}

### get_or_set lock {#get-or-set-lock}

`CacheManager.get_or_set()` turns stampede protection on by default in 0.4.0 ([#280](https://github.com/allen0099/FastAPI-CacheX/issues/280)): on concurrent misses of one key, only one caller runs `factory`, and the others wait for its result. This adds backend round trips on a miss: six instead of two on Redis and Memcached when no other caller waits, plus two per poll for each waiting caller (see [Cost](APP_CACHE.md#cost)). A hit is unchanged. In 0.3.9, a `get_or_set()` call that passes no `lock=`, on a manager created without `lock=`, emits a `FutureWarning` once per manager. That includes the manager `AppCache` creates for you. `CacheManager(lock=None)`, which meant "not chosen" in 0.3.9, raises `TypeError`; pass a bool. `get_or_set(lock=None)` still inherits the manager's setting. Waiting callers now block until the first caller's `factory` returns, bounded by `lock_ttl` (60 s by default); pass `wait_timeout=` to cap that wait (see [Stampede protection](APP_CACHE.md#stampede-protection)). While `factory` runs, the backend holds a short-lived lock key, `lock:<prefix><key>`, which `get_all_keys()` and the monitoring routes list.

Before:

```python
manager = CacheManager(key_prefix="myapp:")
value = await manager.get_or_set("report", build_report)
```

After, to keep the 0.3.x behaviour, either per manager or per call (`lock=True` is the default):

```python
manager = CacheManager(key_prefix="myapp:", lock=False)

value = await manager.get_or_set("report", build_report, lock=False)
```

With `AppCache`, register your own manager at startup, after `BackendProxy.set(...)`:

```python
CacheManagerProxy.set(CacheManager(lock=False))
```

## HTTP caching {#http-caching}

### Cache keys {#cache-keys}

0.4.0 changes the format of every HTTP cache key, in one step so that the upgrade costs a single cache miss ([#271](https://github.com/allen0099/FastAPI-CacheX/issues/271), [#266](https://github.com/allen0099/FastAPI-CacheX/issues/266), [#265](https://github.com/allen0099/FastAPI-CacheX/issues/265), [#269](https://github.com/allen0099/FastAPI-CacheX/issues/269), [#270](https://github.com/allen0099/FastAPI-CacheX/issues/270), [#72](https://github.com/allen0099/FastAPI-CacheX/issues/72)):

- The separator becomes a single `|` (`CACHE_KEY_SEPARATOR`).
- Keys start with the format tag `http:v2|` (`CacheKey.FORMAT_TAG`), so the next format change can remove old keys by pattern: `clear_pattern("http:v2|*")` removes every key of this format.
- The host is normalised: lower-cased, and an empty port or the scheme's default one (`:80` on http, `:443` on https) dropped.
- A query string over 200 bytes (as encoded in the key) is stored as `sha256:` and its hex digest; the path stays readable, and the monitoring routes show the digest.
- Query parameters are sorted by name by default in `@cache`, `invalidate()`, `build_cache_key()` and `CacheKey.from_request()` (`sort_query`, opt-in since 0.3.9), so `?b=2&a=1` and `?a=1&b=2` share one entry.
- One public `CacheKey` type builds, encodes and parses keys. `CACHE_KEY_MIN_PARTS`, `CACHE_KEY_MAX_SPLIT` and `CACHE_KEY_MAX_PARTS` are removed from `fastapi_cachex.routes`; read a key's components with `CacheKey.parse(key)` instead.

```text
Before: GET|||Example.com:80|||/users/1|||page=2
After:  http:v2|GET|example.com|/users/1|page=2
```

What to change:

- `clear_pattern()` patterns that spell out the separator (`"GET|||*|||/users/*"`) need rewriting, and so do patterns that name a host in upper case or with a default port. `clear_path()` and `invalidate()` build the key themselves and need nothing.
- A custom `key_builder` that calls `build_cache_key()` follows automatically, sorting included; pass `sort_query=False` to `build_cache_key()` to keep the order as sent. `@cache` and `invalidate()` now reject `sort_query` passed together with a custom `key_builder`, `False` included (0.3.9 accepted `False` there, where it did nothing): drop it, the builder decides. One that builds the key itself (joining with `CACHE_KEY_SEPARATOR` or hard-coding `|||`) still caches and still works with `invalidate()` and `clear_pattern()`, but its keys lack the `http:v2` tag, so `clear_path()` no longer finds them and the monitoring routes no longer list them. Switch it to `build_cache_key(request, *components)` to keep both.
- A handler whose response depends on the order of the query string as sent, such as a self or pagination link copied from `request.url` or a signature over the raw query, should set `@cache(sort_query=False)`, and pass `sort_query=False` to `invalidate()` for that route. Otherwise the first caller's order is cached and served to callers who sent another. `sort_query=False` works on 0.3.9 already.
- Entries written by 0.3.x are not read by 0.4.0. They expire on their TTL; on Redis and memory you can remove them right after the upgrade with `await backend.clear_pattern("*|||*")`. That pattern matches any key containing `|||`, so check first that none of your own keys (a `CacheManager` key, say) does. Memcached cannot enumerate keys, so there they just expire.

0.3.9 does not warn: nothing in 0.3.x can tell whether a pattern or key builder will match the new format, and the only runtime cost is the one-off miss. It does not warn about `sort_query` passed with a custom `key_builder` either, but that raises `CacheXError` when the decorator is applied, usually at import, not at request time.

### Repeated headers {#cache-entry-headers}

0.4.0 stores every line of a header the handler sends more than once (several `Link` headers, say) instead of only the last one ([#105](https://github.com/allen0099/FastAPI-CacheX/issues/105)). `CacheEntry.headers` becomes a tuple of `(name, value)` pairs in the order sent, instead of `dict[str, str] | None`; no headers is `()`. A replayed value is no longer joined or split, so a value holding a comma stays one line. This affects custom backends and code that reads `CacheEntry`. Code that builds one can keep passing a `dict` (or any iterable of pairs, or `None`): the constructor converts it, though a type checker flags the `dict`.

```python
# Before
entry.headers["link"]

# After
[value for name, value in entry.headers if name == "link"]
```

Redis and Memcached store the headers as a JSON list of `[name, value]` lines. 0.4.0 still decodes the object 0.3.x wrote, but 0.3.x fails with a `500` on an entry 0.4.0 wrote with any header. For `@cache` with the default key builder, or one that calls `build_cache_key()`, a rolling deploy needs nothing extra: the [cache key format](#cache-keys) changes in the same release, so neither version looks up an HTTP entry the other wrote. A custom `key_builder` that builds the key itself gives the same key in both versions; for such a route, keep 0.3.x instances off the shared backend during the rollout, or switch the builder to `build_cache_key()` first. The same applies to an application that stores `CacheEntry` values under its own keys and shares them across versions.

### Monitoring routes {#add-routes}

`add_routes()` requires `dependencies` in 0.4.0, and `include_content_preview` defaults to `False` ([#298](https://github.com/allen0099/FastAPI-CacheX/issues/298)). 0.3.9 emits a `UserWarning` when `dependencies` is left out. `dependencies` and `include_content_preview` are keyword-only; leaving `dependencies` out, or passing `None`, raises `TypeError`. 0.3.9 did not warn about the two cases that break without it: passing these arguments by position, which now raises `TypeError`, and relying on the preview default while passing `dependencies`, which now hides the previews; pass `include_content_preview=True` to keep them.

```python
# Before
add_routes(app)

# After
add_routes(app, dependencies=[Depends(verify_admin)])
# or, unguarded on purpose (local or test setups), keeping the previews:
add_routes(app, dependencies=[], include_content_preview=True)
```

## Backends {#backends}

### Redis encoding {#redis-encoding}

The Redis client reads raw bytes in 0.4.0, and the `encoding` option is removed from `AsyncRedisCacheBackend` and `RedisConfig` ([#126](https://github.com/allen0099/FastAPI-CacheX/issues/126)). Entries were always written as UTF-8, so leaving it out changes nothing. In 0.3.9, a UTF-8 `encoding` passed to `AsyncRedisCacheBackend` emits a `DeprecationWarning`, and any other value emits only its `RuntimeWarning`, which also announces the removal. A `RedisConfig` that sets `encoding` emits the `DeprecationWarning` when it is passed to `load_from_config()`, plus the `RuntimeWarning` for a value other than UTF-8. In 0.4.0, passing `encoding` or `decode_responses` to `AsyncRedisCacheBackend` raises `TypeError`, and `RedisConfig` ignores an `encoding` value like any other unknown field. `decode_responses` gets no warning in 0.3.9: it only ever accepted `True`, its default, so passing it had no effect.

```python
# Before
AsyncRedisCacheBackend(host="redis", encoding="utf-8")
RedisConfig(host="redis", encoding="utf-8")

# After
AsyncRedisCacheBackend(host="redis")
RedisConfig(host="redis")
```

### Redis clear_pattern {#redis-clear-pattern}

Before 0.3.8, a Redis `clear_pattern()` pattern that started with the backend's `key_prefix` was matched with the prefix stripped. 0.3.x still retries that way when the pattern clears nothing, with a `DeprecationWarning`; 0.4.0 removes the retry ([#125](https://github.com/allen0099/FastAPI-CacheX/issues/125)). Patterns match the logical key:

```python
# Before
await backend.clear_pattern("fastapi_cachex:GET|||*")

# After
await backend.clear_pattern("GET|||*")  # "http:v2|GET|*" with 0.4.0's key format
```

### delete() return value {#backend-delete}

`BaseCacheBackend.delete()` returns whether a key was removed in 0.4.0, instead of `None` ([#71](https://github.com/allen0099/FastAPI-CacheX/issues/71)), and the base `delete_many()` fallback counts the keys that existed rather than the ones attempted. A third-party backend that returns `None` fails type checking and makes that count wrong. 0.3.9 does not warn: a subclass cannot declare `-> bool` today without a type error against the 0.3.x base class, and nobody uses the `None`.

```python
# Before
class MyBackend(BaseCacheBackend):
    async def delete(self, key: str) -> None:
        await self._client.delete(key)


# After
class MyBackend(BaseCacheBackend):
    async def delete(self, key: str) -> bool:
        return await self._client.delete(key) > 0
```

Callers that need the answer in 0.3.x can use `await backend.get_and_delete(key) is not None`.

### BackendProxy {#backend-proxy}

`BackendProxy.get_backend()` and `set_backend()` are removed ([#70](https://github.com/allen0099/FastAPI-CacheX/issues/70)); 0.3.x already emits a `DeprecationWarning`.

```python
# Before
BackendProxy.set_backend(backend)
backend = BackendProxy.get_backend()

# After
BackendProxy.set(backend)
backend = BackendProxy.get()
```

### CacheError {#cache-error}

The `CacheError` alias is removed ([#130](https://github.com/allen0099/FastAPI-CacheX/issues/130)); 0.3.8 and 0.3.9 already emit a `DeprecationWarning` when it is imported.

```python
# Before
from fastapi_cachex.exceptions import CacheError

# After
from fastapi_cachex.exceptions import CacheXError
```

### memcache extra {#memcache-extra}

The `memcache` extra, an alias of `memcached`, is removed ([#202](https://github.com/allen0099/FastAPI-CacheX/issues/202)). The failure is quiet: pip and uv only warn about an unknown extra and install `fastapi-cachex` without `pymemcache`, so the error appears when `MemcachedBackend` is constructed. 0.3.9 cannot warn, because an extra is resolved by the installer and the library never sees which one was requested.

```bash
# Before
pip install "fastapi-cachex[memcache]"

# After
pip install "fastapi-cachex[memcached]"
```
