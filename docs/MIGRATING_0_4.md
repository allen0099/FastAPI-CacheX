# Migrating to 0.4.0 {#migrating-to-040}

0.3.9 is the last 0.3.x release. 0.4.0 contains breaking changes, collected in the [0.4.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.4.0). This page lists every one of them, what to change, and whether 0.3.9 already warns about it. Some 0.4.0 designs are not final yet; where an issue leaves a detail open, the section says what is known and what is still undecided.

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

Every warning below names the setting to change and links to its issue. `FutureWarning` announces a default that changes behaviour and is shown by default; `DeprecationWarning` announces a removal; `UserWarning` flags a configuration that is already wrong today and that 0.4.0 no longer accepts. Once 0.3.9 runs without these warnings, the changes in the "Warned in 0.3.9" column are done; the rest of this page covers what no warning can detect.

## Summary {#summary}

| Change | Issue | Warned in 0.3.9 | Section |
|--------|-------|-----------------|---------|
| Session cookie defaults to `__Host-session` with `Secure` | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | `FutureWarning` | [Session cookie](#session-cookie) |
| Contradictory `__Host-` / `__Secure-` cookie settings are rejected | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | `UserWarning` | [Session cookie](#session-cookie) |
| Explicit `login()` / `logout()`, read-only `Session.user` | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | No | [Login and logout](#login-logout) |
| `get_or_set()` locks by default | [#280](https://github.com/allen0099/FastAPI-CacheX/issues/280) | `FutureWarning` | [get_or_set lock](#get-or-set-lock) |
| `get_session_manager` resolves through `SessionManagerProxy` | [#131](https://github.com/allen0099/FastAPI-CacheX/issues/131) | `FutureWarning` | [get_session_manager](#get-session-manager) |
| `add_routes()` requires `dependencies`, no content preview by default | [#298](https://github.com/allen0099/FastAPI-CacheX/issues/298) | `UserWarning` | [Monitoring routes](#add-routes) |
| Redis `encoding` option removed | [#126](https://github.com/allen0099/FastAPI-CacheX/issues/126) | `DeprecationWarning` | [Redis encoding](#redis-encoding) |
| JWT HMAC secrets shorter than the hash output are rejected | [#129](https://github.com/allen0099/FastAPI-CacheX/issues/129) | `UserWarning` | [JWT secret length](#jwt-secret) |
| `SessionMiddleware` removed | [#69](https://github.com/allen0099/FastAPI-CacheX/issues/69) | `DeprecationWarning` | [SessionMiddleware](#session-middleware) |
| `BackendProxy.get_backend()` / `set_backend()` removed | [#70](https://github.com/allen0099/FastAPI-CacheX/issues/70) | `DeprecationWarning` | [BackendProxy](#backend-proxy) |
| `CacheError` removed | [#130](https://github.com/allen0099/FastAPI-CacheX/issues/130) | `DeprecationWarning` | [CacheError](#cache-error) |
| Redis `clear_pattern()` prefix-stripped retry removed | [#125](https://github.com/allen0099/FastAPI-CacheX/issues/125) | `DeprecationWarning` | [Redis clear_pattern](#redis-clear-pattern) |
| `UserSessionDep` requires a user | [#127](https://github.com/allen0099/FastAPI-CacheX/issues/127) | No | [UserSessionDep](#user-session-dep) |
| `memcache` extra removed | [#202](https://github.com/allen0099/FastAPI-CacheX/issues/202) | No | [memcache extra](#memcache-extra) |
| `BaseCacheBackend.delete()` returns `bool` | [#71](https://github.com/allen0099/FastAPI-CacheX/issues/71) | No | [delete() return value](#backend-delete) |
| `CacheEntry.headers` becomes a list of pairs | [#105](https://github.com/allen0099/FastAPI-CacheX/issues/105) | No | [Repeated headers](#cache-entry-headers) |
| HTTP cache key format | [#271](https://github.com/allen0099/FastAPI-CacheX/issues/271), [#270](https://github.com/allen0099/FastAPI-CacheX/issues/270), [#269](https://github.com/allen0099/FastAPI-CacheX/issues/269), [#266](https://github.com/allen0099/FastAPI-CacheX/issues/266), [#265](https://github.com/allen0099/FastAPI-CacheX/issues/265), [#72](https://github.com/allen0099/FastAPI-CacheX/issues/72) | No | [Cache keys](#cache-keys) |
| Conditional session writes | [#128](https://github.com/allen0099/FastAPI-CacheX/issues/128) | No | [Session writes](#session-writes) |
| Cookies in `token_source_priority` | [#75](https://github.com/allen0099/FastAPI-CacheX/issues/75) | No | [Token sources](#token-source-priority) |

## Sessions {#sessions}

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

0.4.0 also rejects contradictory settings: a `__Host-` name without `cookie_https_only=True`, with a `cookie_path` other than `"/"` or with a `cookie_domain`, and a `__Secure-` name without `cookie_https_only=True`. Browsers already refuse such a cookie, so the session never sticks; 0.3.9 emits a `UserWarning` when `SessionConfig` is built with one of them.

### Login and logout {#login-logout}

0.4.0 makes becoming authenticated go through one explicit API that always issues a new session ID ([#256](https://github.com/allen0099/FastAPI-CacheX/issues/256)). Known so far:

- `login(request, user)` (in 0.3.9 already, `from fastapi_cachex.session import login`) attaches the user and rotates the ID. Use it today instead of setting `session.user` yourself.
- A logout API is added; `request.session.clear()` keeps meaning logout.
- `Session.user` becomes read-only outside these calls. Code that assigns it directly breaks.
- A login starts a new session instead of promoting the anonymous one. Which data is carried over (all by default, or a `keep=` list) is not decided yet.
- For a few seconds after a rotation the old ID resolves to the new session, so in-flight requests carrying the old token do not fail.
- `rotate_session_id()` stays for privilege changes without a new user, possibly renamed.

The exact signatures are not final, so 0.3.9 does not warn about them. An application that decides "logged in" from its own `request.session` keys (`request.session.get("user_id")`) is outside what the library can see; use the library's identity (`session.user`, `AuthenticatedSession`) or rotate the ID yourself.

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

`get_session_manager` (and `SessionManagerDep`, `ClientIPDep` and `rotate_session_id()`, which use it) currently returns the manager the middleware stored on `app.state`. 0.4.0 resolves it through `SessionManagerProxy` only, like `BackendProxy` and `CacheManagerProxy` ([#131](https://github.com/allen0099/FastAPI-CacheX/issues/131)). In 0.3.9 it emits a `FutureWarning`, once per app, when the proxy holds no manager or a different one.

Before:

```python
session_manager = SessionManager(backend, config)
app.add_middleware(FastAPICacheXSessionMiddleware, session_manager=session_manager)
```

After:

```python
session_manager = SessionManager(backend, config)
SessionManagerProxy.set(session_manager)
# The middleware picks the manager up from the proxy.
app.add_middleware(FastAPICacheXSessionMiddleware)
```

### UserSessionDep {#user-session-dep}

`UserSessionDep` is an alias of `SessionDep` and admits anonymous sessions. In 0.4.0 it requires a session with a user, like `AuthenticatedSession` ([#127](https://github.com/allen0099/FastAPI-CacheX/issues/127)), so anonymous requests to routes that use it get `401`. 0.3.9 does not warn: a type alias has no hook that runs when it is used, and a warning on import would fire for everyone. Pick the dependency that says what you mean:

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

With `token_format="jwt"`, 0.4.0 raises at startup when `jwt_algorithm` is `HS384` or `HS512` and `secret_key` is shorter than 48 or 64 bytes (RFC 7518 section 3.2) ([#129](https://github.com/allen0099/FastAPI-CacheX/issues/129)). 0.3.x emits a `UserWarning` when `JWTTokenSerializer` is built. Use a longer key, or `HS256`:

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

0.4.0 writes sessions conditionally ([#128](https://github.com/allen0099/FastAPI-CacheX/issues/128)): a request that loaded a session before another request deleted, invalidated or rotated it can no longer bring the record back when it saves. No code change is needed. A custom backend has to support the write-if-present primitive this adds; its shape is not decided yet.

### Token sources {#token-source-priority}

`SessionConfig.token_source_priority` accepts `"cookie"` next to `"header"` and `"bearer"` in 0.4.0 ([#75](https://github.com/allen0099/FastAPI-CacheX/issues/75)), and `FastAPICacheXSessionMiddleware` resolves the token by walking the list. Existing lists keep working; today the cookie is read after the header and bearer sources. Whether the default list gains `"cookie"`, and in which position, is not decided yet.

## Application cache {#application-cache}

### get_or_set lock {#get-or-set-lock}

`CacheManager.get_or_set()` turns stampede protection on by default in 0.4.0 ([#280](https://github.com/allen0099/FastAPI-CacheX/issues/280)): on concurrent misses of one key, only one caller runs `factory`, and the others wait for its result. This adds backend round trips on a miss (see [Stampede protection](APP_CACHE.md#stampede-protection)). In 0.3.9, a `get_or_set()` call that passes no `lock=`, on a manager created without `lock=`, emits a `FutureWarning` once per manager. That includes the manager `AppCache` creates for you.

Before:

```python
manager = CacheManager(key_prefix="myapp:")
value = await manager.get_or_set("report", build_report)
```

After, either per manager or per call:

```python
manager = CacheManager(key_prefix="myapp:", lock=False)  # keep today's behaviour
manager = CacheManager(key_prefix="myapp:", lock=True)  # opt in now

value = await manager.get_or_set("report", build_report, lock=True)
```

With `AppCache`, register your own manager at startup:

```python
CacheManagerProxy.set(CacheManager(lock=True))
```

## HTTP caching {#http-caching}

### Cache keys {#cache-keys}

0.4.0 changes the format of every HTTP cache key, in one step so that the upgrade costs a single cache miss ([#271](https://github.com/allen0099/FastAPI-CacheX/issues/271), [#266](https://github.com/allen0099/FastAPI-CacheX/issues/266), [#265](https://github.com/allen0099/FastAPI-CacheX/issues/265), [#269](https://github.com/allen0099/FastAPI-CacheX/issues/269), [#270](https://github.com/allen0099/FastAPI-CacheX/issues/270), [#72](https://github.com/allen0099/FastAPI-CacheX/issues/72)):

- The separator becomes a single `|` (`CACHE_KEY_SEPARATOR`).
- Keys start with a format tag, such as `http:v2|`, so the next format change can remove old keys by pattern.
- The host is normalised: lower-cased, and the scheme's default port (`:80`, `:443`) dropped.
- A long query string (over about 200 bytes) is stored as `sha256:` and its hex digest; the path stays readable.
- Query parameters are sorted, as `@cache(sort_query=True)` does since 0.3.9; whether that becomes the default or stays opt-in is not decided yet.
- One `CacheKey` type encodes and parses keys; the key-parsing internals of `routes.py` change.

```text
Before: GET|||Example.com:80|||/users/1|||page=2
After:  http:v2|GET|example.com|/users/1|page=2   (exact tag not final)
```

What to change:

- `clear_pattern()` patterns that spell out the separator (`"GET|||*|||/users/*"`) need rewriting. `clear_path()` and `invalidate()` build the key themselves and need nothing.
- A custom `key_builder` that calls `build_cache_key()` or joins with `CACHE_KEY_SEPARATOR` follows automatically; one that hard-codes `|||` does not.
- Entries written by 0.3.x are not read by 0.4.0. They expire on their TTL; on Redis and memory you can remove them right after the upgrade with `await backend.clear_pattern("*|||*")`. Memcached cannot enumerate keys, so there they just expire.

0.3.9 does not warn: nothing in 0.3.x can tell whether a pattern or key builder will match the new format, and the only runtime cost is the one-off miss.

### Repeated headers {#cache-entry-headers}

0.4.0 stores every line of a header the handler sends more than once (several `Link` headers, say) instead of only the last one ([#105](https://github.com/allen0099/FastAPI-CacheX/issues/105)). `CacheEntry.headers` becomes an ordered list of `(name, value)` pairs instead of `dict[str, str]`. This affects custom backends and code that builds or reads `CacheEntry`:

```python
# Before
entry.headers["link"]

# After
[value for name, value in entry.headers if name == "link"]
```

0.4.0 still reads entries written by 0.3.x, but 0.3.x cannot read entries written by 0.4.0. In a rolling deploy where both versions share a backend, clear the HTTP cache once all instances run 0.4.0 (or keep the old instances away from the shared cache during the rollout). Whether the stored format carries a version marker instead is not decided yet.

### Monitoring routes {#add-routes}

`add_routes()` requires `dependencies` in 0.4.0, and `include_content_preview` defaults to `False` ([#298](https://github.com/allen0099/FastAPI-CacheX/issues/298)). 0.3.x emits a `UserWarning` when `dependencies` is left out.

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

The Redis client reads raw bytes in 0.4.0, and the `encoding` option is removed from `AsyncRedisCacheBackend` and `RedisConfig` ([#126](https://github.com/allen0099/FastAPI-CacheX/issues/126)). Entries were always written as UTF-8, so leaving it out changes nothing. In 0.3.9, passing `encoding` at all emits a `DeprecationWarning` (and a value other than UTF-8 keeps its `RuntimeWarning`).

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
await backend.clear_pattern("GET|||*")  # "GET|*" with 0.4.0's key format
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

The `CacheError` alias is removed ([#130](https://github.com/allen0099/FastAPI-CacheX/issues/130)); 0.3.x already emits a `DeprecationWarning` when it is imported.

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
