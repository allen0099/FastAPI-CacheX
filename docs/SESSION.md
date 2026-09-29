# Session Management Extension

FastAPI-CacheX Session Management provides complete user session handling, including signed
tokens, sliding expiration, and optional IP/User-Agent binding. Session contents always live in
the cache backend; the client only holds a single signed token.

**How the token travels depends on which middleware you install:**

| Middleware | Token source | Response side | Status |
|------------|--------------|---------------|--------|
| `FastAPICacheXSessionMiddleware` | Custom header (default `X-Session-Token`) / `Authorization: Bearer` / **cookie** (default name `session`) | Routed by source: a request that sent a header or bearer token (even one that no longer resolves) gets its token in the response header; otherwise (a cookie, or no token at all) it gets `Set-Cookie` | **Recommended** |
| `SessionMiddleware` | Custom header / `Authorization: Bearer`; **no cookie support** | A renewed token, or one for a regenerated ID, is sent back in the response header | Deprecated, **removed in 0.4.0** |

**Use `FastAPICacheXSessionMiddleware` for all new projects.** It covers every transport of
`SessionMiddleware` (it reads `X-Session-Token` and `Authorization: Bearer` in the same way) and
adds cookie support. Since 0.3.1, `SessionMiddleware` emits a `DeprecationWarning` when it is
constructed, and it will be **removed in 0.4.0**. Both middlewares feed the same session
dependencies (`get_session`, `get_optional_session`, `require_session`), so migrating usually only
means changing the `add_middleware` line; existing clients that send the token in a header need no
changes.

The six `cookie_*` settings of `SessionConfig` (`cookie_name`, `cookie_max_age`, `cookie_path`,
`cookie_same_site`, `cookie_https_only`, `cookie_domain`) are **read only by
`FastAPICacheXSessionMiddleware`**; setting them has no effect when `SessionMiddleware` is installed.

Complete runnable examples: [`examples/session_login.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_login.py) and [`examples/session_jwt.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt.py).

## Features

- ✅ **Session lifecycle management**: create, read, update, delete, invalidate
- ✅ **Security**:
    - HMAC-SHA256 token signing
    - IP address binding (optional)
    - User-Agent binding (optional)
    - Session ID regeneration after login
- ✅ **Multiple token sources**: custom header, `Authorization: Bearer`, cookie
  (cookies are supported by `FastAPICacheXSessionMiddleware` only)
- ✅ **Optional JWT format**: use a JWT as the session token (requires the `jwt` extra)
- ✅ **Sliding expiration**, plus an optional absolute timeout
- ✅ **Flash messages**: pass messages across requests
- ✅ **Multiple backends**: Redis, Memcached, in-memory
- ✅ **API-first or browser-based architectures**: the client can keep the token itself
  (header/bearer), or leave it to the browser as a cookie (`FastAPICacheXSessionMiddleware`)

## Quick Start

### 1. Installation

Session management is built into FastAPI-CacheX:

```bash
uv add fastapi-cachex
```

To enable the JWT token format:

```bash
uv add "fastapi-cachex[jwt]"
```

### 2. Basic Usage

This is [`examples/session_api.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_api.py): an API client logs in,
keeps the token it gets back and sends it on later requests.

<!-- fmt:off -->
```python
--8<-- "examples/session_api.py"
```
<!-- fmt:on -->

The example also registers the manager on `SessionManagerProxy`, where
`get_session_manager` looks for it from 0.4.0 (see
[Migrating to 0.4.0](MIGRATING_0_4.md#get-session-manager)). With the manager
there, the middleware can pick it up instead of taking it as an argument. When
`config` is omitted, the middleware uses `session_manager.config`:

```python
app.add_middleware(FastAPICacheXSessionMiddleware)  # picked up from the proxy
```

`get_session` (and its alias `require_session`) raises `401 Authentication required` with a
`WWW-Authenticate: Bearer` header when the request carries no valid session. A token that is
malformed, forged, expired, invalidated or fails a binding check is never an error at the
middleware level: the request simply proceeds without a session.

The session object the dependencies return is the backend `Session` model. A session created by
`FastAPICacheXSessionMiddleware` from `request.session` (see the Migration section below) is
anonymous, so `session.user` is `None`.

`get_session` accepts such a session, so it only proves the request carries *a* session, not
that anyone logged in. Any visitor who reaches a route that writes to `request.session` (a cart,
a CSRF value) gets one. Guard routes that need a logged-in user with `require_user_session` (or
its annotated form `AuthenticatedSession`), which also answers `401` when `session.user` is
`None`, as `/profile` above does. `/logout` only deletes the session, so `SessionDep` (the
annotated form of `get_session`) is enough there, and `/public` uses `OptionalSession`
(`get_optional_session`), which gives `None` instead of answering `401`.

`UserSessionDep` does not check for a user despite its name; it is an alias of `SessionDep`
until 0.4.0, which is planned to make it require one.

Under `FastAPICacheXSessionMiddleware`, log a user in with `await login(request, user)`. It
attaches the `SessionUser` that `require_user_session` / `AuthenticatedSession` check, under a
new session ID, and the middleware sends the token; see
[Regenerate the Session ID After Login](#5-regenerate-the-session-id-after-login). The `/login`
above instead hands an API client its token in the body: `create_session(user=...)` sets
`session.user` too, but the middleware sends nothing for a session it did not load or start.
Keys written to `request.session` (`request.session["user_id"] = ...`) are application data:
the library does not treat them as a login, so `AuthenticatedSession` still answers `401` for
such a session.

### 3. Full Example (Redis Backend)

This is [`examples/session_redis.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_redis.py). Like
[`examples/redis_backend.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/redis_backend.py), it reads the Redis
settings from `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` and `REDIS_PASSWORD`.

<!-- fmt:off -->
```python
--8<-- "examples/session_redis.py"
```
<!-- fmt:on -->

Changes made to a `Session` object inside a handler (flash messages, `session.data`,
`session.user`) are only persisted when you call `session_manager.update_session(session)`.

`delete_user_sessions()` and `clear_expired_sessions()` enumerate every key in the backend via
`get_all_keys()` and load each session under `backend_key_prefix`, so their cost grows with the
size of the backend. On the Memcached backend, which cannot enumerate keys, they find nothing and
return `0` (with a `RuntimeWarning` from the backend).

`clear_expired_sessions()` removes every session that can no longer be used: those past their
`expires_at`, and those no longer `ACTIVE` (invalidated with `invalidate_session()`, or marked
expired by an earlier read) that would otherwise stay in the backend until their TTL runs out.
Both methods delete what they find with a single `backend.delete_many()` call.

## Migration: SessionMiddleware → FastAPICacheXSessionMiddleware

`SessionMiddleware` has been deprecated since 0.3.1 (it emits a `DeprecationWarning` when
constructed) and will be removed in 0.4.0. Use `FastAPICacheXSessionMiddleware` instead:

- **`SessionMiddleware`** (a `BaseHTTPMiddleware`): passes the token in a custom header (default
  `X-Session-Token`) and/or `Authorization: Bearer`, suited to API-first architectures where the
  client manages the token. Cookie transport is not supported.
- **`FastAPICacheXSessionMiddleware`** (a pure ASGI middleware): compatible with Starlette's
  built-in `SessionMiddleware`, exposing the same dict-like `request.session`. It passes the signed
  session token in a cookie (default cookie name `session`), while the session contents are stored
  in the backend (the cache backend of the `SessionManager`) rather than encoded into the cookie
  itself as Starlette's own implementation does. Token resolution is "header first, cookie
  second": it reads the custom header (default `X-Session-Token`) and/or `Authorization: Bearer`
  first and only falls back to the cookie when neither is present, so clients that used
  `X-Session-Token` with `SessionMiddleware` keep working unchanged. The response side is routed
  by source too: when the request sent a header or bearer token (even one that no longer
  resolves), a new or renewed token is sent back in the `header_name` response header and no
  `Set-Cookie` is emitted; a token that arrived in a cookie (or a brand-new anonymous session for a
  request without a token) uses `Set-Cookie`.

Both middlewares put the loaded `Session` object into `request.state`, so the existing session
dependencies `get_session`, `get_optional_session`, `require_session` and `require_user_session`
work under either middleware without any changes:

```python
from fastapi import Depends
from fastapi_cachex.session import FastAPICacheXSessionMiddleware, require_user_session

app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=manager, config=config
)


@app.get("/me")
async def me(session=Depends(require_user_session)):
    return {"user_id": session.user.user_id}
```

### `request.session` with `FastAPICacheXSessionMiddleware`

`request.session` is a view of the backend session's `data` dict:

- Writing to `request.session` when no session was loaded creates a new **anonymous** session
  (`SessionManager.create_anonymous_session()`, with IP/User-Agent bindings applied as
  configured) and sends its token back through the request's transport.
- Modifying it on a loaded session saves the new contents to the backend via `update_session()`,
  replacing `Session.data` with the dict's contents.
- Clearing it (`request.session.clear()`) on a loaded session logs out: the backend session is
  deleted even if its data was already empty, and a cookie client also receives a `Set-Cookie`
  that expires the cookie. Keys written after `clear()` in the same request go into a new
  anonymous session under a new ID.
- Removing the last key with `del` or `pop()` is not a logout. A session with a user is saved
  with empty data; an anonymous one holds nothing and is deleted, as with `clear()`.
- Logging in by writing to `request.session` keeps the session ID the request arrived with.
  With Starlette's middleware the cookie *is* the session, so the login response replaces
  whatever cookie was planted; here the cookie only names a server-side record, and a planted
  one would be logged in along with the victim. Log in with `await login(request, user)`, which
  gives the session a new ID and attaches the user (see
  [Regenerate the Session ID After Login](#5-regenerate-the-session-id-after-login)).
- Any access to `request.session`, or a read through the session dependencies (`get_session`,
  `get_optional_session` and those built on them, such as `AuthenticatedSession`), adds `Vary`
  for every request header read to find the token:
  the headers checked in `token_source_priority` order (`header_name`, and `Authorization` when
  bearer tokens are enabled) up to the one that carried the token. `Cookie` is added only when
  no header carried a token, because only then is the cookie read.
- A response that carries a session token (a new session, a sliding renewal, a regenerated ID)
  or a `Set-Cookie` that expires the session cookie is never cacheable. The middleware sets
  `Cache-Control: private, no-store`, replacing whatever the route set (a `@cache(public=True)`
  route included), and adds the same `Vary` names as above even when the handler never touched
  `request.session`. Otherwise a CDN or reverse proxy could store the token and hand it to the
  next visitor. Responses without a token keep their headers. The deprecated `SessionMiddleware`
  does the same when it sends a token in its response header.
- `@cache` does not read or write its backend for a request that arrived with a session (one
  the middleware loaded, from any transport, with or without a user, or a non-empty
  `request.session`), and answers it with `private`, as for `Authorization`. `public=True`
  shares the route across sessions; `cache_authorized=True` with a `key_builder` that includes
  the session's user caches per user, still answered with `private`. See
  [Authenticated endpoints](HTTP_CACHING.md#authenticated-endpoints).

The cookie is always `HttpOnly`; `Secure`, `SameSite`, `Domain`, `Path` and `Max-Age` follow the
`cookie_*` settings (`cookie_max_age=None`, or `0`, omits `Max-Age`).

## Configuration

### SessionConfig

`SessionConfig` is a Pydantic model that rejects unknown fields (`extra="forbid"`), so a typo in
a field name raises a `ValidationError`. The values below are the defaults, except `secret_key`,
which is required.

```python
SessionConfig(
    # Session lifetime
    session_ttl=3600,  # session TTL (seconds)
    absolute_timeout=None,  # hard cap measured from created_at (seconds); None = no cap
    sliding_expiration=True,  # sliding expiration
    sliding_threshold=0.5,  # 0.0-1.0; renew once less than this fraction of the TTL remains
    # Token sources (API-first architecture)
    token_format="simple",  # "simple" (default) or "jwt"
    header_name="X-Session-Token",
    use_bearer_token=True,
    token_source_priority=["header", "bearer"],  # only these two values (see below)
    # JWT (used when token_format == "jwt")
    jwt_algorithm="HS256",  # "none" is rejected
    jwt_issuer=None,  # if set, iss is written and verified on parsing
    jwt_audience=None,  # if set, aud is written and verified on parsing
    jwt_leeway=0,  # tolerance in seconds for exp/iat checks (nbf is neither issued nor verified)
    # Security
    secret_key="...",  # required: at least 32 characters
    ip_binding=False,  # IP binding
    user_agent_binding=False,  # User-Agent binding
    trusted_proxies=[],  # trusted reverse proxy addresses (see "Client IP and reverse proxies")
    # Backend
    backend_key_prefix="session:",
    # Cookies (read only by FastAPICacheXSessionMiddleware)
    cookie_name="session",  # "__Host-session" from 0.4.0
    cookie_max_age=14
    * 24
    * 60
    * 60,  # None = no Max-Age (cookie ends with the browser session)
    cookie_path="/",
    cookie_same_site="lax",  # "lax" / "strict" / "none" ("none" needs cookie_https_only=True)
    cookie_https_only=False,  # True adds the Secure flag; True from 0.4.0
    cookie_domain=None,  # None = no Domain attribute
)
```

#### Cookie defaults change in 0.4.0 {#cookie-defaults-change-in-040}

0.4.0 names the session cookie `__Host-session` and sets the `Secure` flag by default. Browsers accept a `__Host-` cookie only when it is `Secure`, has `Path=/` and no `Domain`, and never from a subdomain, which removes the usual way to plant a session cookie (session fixation). The new name also means every browser holding a `session` cookie is logged out once after the upgrade.

Until then, `FastAPICacheXSessionMiddleware` emits a `FutureWarning` when it is constructed (when the app builds its middleware stack, at startup or on the first request) and its config leaves `cookie_name` or `cookie_https_only` at the default. Set both to silence it:

- `cookie_name="session", cookie_https_only=False` keeps the current cookie (and keeps working after the upgrade, e.g. for local development over plain HTTP);
- `cookie_name="__Host-session", cookie_https_only=True` switches now, over HTTPS.

A `__Host-` name with `cookie_https_only=False`, a `cookie_path` other than `/` or a `cookie_domain` (and a `__Secure-` name without `cookie_https_only=True`) emits a `UserWarning`, because browsers refuse such a cookie; 0.4.0 rejects the combination. See [Migrating to 0.4.0](MIGRATING_0_4.md#session-cookie).

Sessions expire after `session_ttl` seconds. With `sliding_expiration`, each request that finds
less than `session_ttl * sliding_threshold` seconds remaining extends the expiry to a full
`session_ttl` again and issues a renewed token, which the middleware sends back to the client
(response header or `Set-Cookie`, see the table above). Header/bearer clients should replace their
stored token when the response carries the `header_name` header. `absolute_timeout` ends the
session that many seconds after it was created, regardless of sliding renewals: the expiry,
the backend TTL and a JWT's `exp` never go past `created_at + absolute_timeout`, and once
the expiry reaches that cap no further renewed tokens are issued.

#### `token_source_priority` accepts only `"header"` and `"bearer"`

The field's type is `list[Literal["header", "bearer"]]`; passing `"cookie"` is rejected by
Pydantic with a `ValidationError`. The cookie is **not** part of the priority order:
`FastAPICacheXSessionMiddleware` resolves tokens in a fixed order — it first reads
header/bearer following `token_source_priority`, and only falls back to the cookie when neither
yields a token. This is deliberate: the response side is routed by the token's source (header in,
header out; cookie in, `Set-Cookie` out), and mixing the cookie into the same priority list would
make a `["cookie"]` setting fail silently on the deprecated `SessionMiddleware`. Once
`SessionMiddleware` is removed in 0.4.0, all three sources may be described by a single priority
list.

**Header/bearer clients** should store the token in `localStorage` or `sessionStorage` and send
it as `Authorization: Bearer <token>` or `X-Session-Token: <token>`. **Cookie clients** (browsers)
do not need to handle the token themselves, but beware of CSRF: the browser attaches cookies
automatically, so combine `cookie_same_site` with your own CSRF protection.

### Using the JWT Token Format

With `token_format="jwt"`, session tokens are issued as JWTs carrying these claims:

- `sid`: session ID (custom claim, maps to the server-side session)
- `iat`: issued-at time (epoch seconds)
- `exp`: expiry time — the session's current `expires_at` (so it moves with sliding renewal),
  falling back to `iat + session_ttl`
- `iss`/`aud`: written when configured, and verified on parsing

Example configuration:

```python
config = SessionConfig(
    secret_key="your-secret-key-at-least-32-characters",
    token_format="jwt",
    jwt_algorithm="HS256",
    jwt_issuer="your-issuer",
    jwt_audience="your-audience",
)
```

`jwt_algorithm` must be one of `HS256`, `HS384`, `HS512`, `RS256`, `RS384`, `RS512`, `ES256`,
`ES384`, `ES512`, `PS256`, `PS384`, `PS512` or `EdDSA`; anything else (including `none`) raises a
`ValidationError`. The built-in serializer signs and verifies with the same `secret_key`, so it
only supports `HS256`, `HS384` and `HS512`: with an asymmetric algorithm, `SessionManager` raises
`ValueError` unless you pass a custom `token_serializer` that holds the key pair.

An HMAC key must be at least as long as the hash output (RFC 7518 §3.2): 32 bytes for `HS256`,
48 for `HS384` and 64 for `HS512`, counted after UTF-8 encoding. `secret_key` only has to be 32
characters, so with `HS384` or `HS512` a shorter key makes the serializer emit a `UserWarning`
once when it is built (PyJWT 2.11 and later also warn with `InsecureKeyLengthWarning` whenever they
sign or verify a token). Use a longer key, for example `secrets.token_urlsafe(64)`.

Security notes:

- The server keeps **stateful** sessions (the JWT is only a credential carrying the `sid`), so no
  sensitive data needs to go into the token
- Parsing verifies the signature and the required claims (`sid`/`iat`/`exp`, plus `iss`/`aud`
  when configured)
- Use HTTPS and a key rotation strategy in production (an advanced scheme with `kid` and
  multiple keys is a possible future extension)

**Advanced topics**: for the design of the JWT claims, why optional claims such as `jti`/`nbf` are
not implemented, and how to add custom claims, see the
**[JWT Claims implementation notes and extension guide](JWT_CLAIMS.md)**.

## Security Best Practices

### 1. Secret Key

```python
import secrets

# Generate a secure secret key
secret_key = secrets.token_urlsafe(32)

config = SessionConfig(secret_key=secret_key)
```

`secret_key` is stored as a `SecretStr` and must be at least 32 characters long (at least 48
bytes for `jwt_algorithm="HS384"` and 64 for `"HS512"`; see the JWT section above). Load it from the
environment or a secret store rather than hard-coding it; changing it invalidates every token
issued so far.

### 2. HTTPS Only

Always transport tokens over HTTPS in production. For cookie clients, mark the cookie `Secure`:

```python
config = SessionConfig(
    secret_key="...",
    cookie_name="__Host-session",  # browsers refuse it without Secure, Path=/, no Domain
    cookie_https_only=True,  # adds the Secure flag to the session cookie
)
```

**Client-side notes**:

- Only send the token over HTTPS
- The session cookie set by `FastAPICacheXSessionMiddleware` is always `HttpOnly`, so page scripts
  cannot read it; a token kept in `localStorage`/`sessionStorage` is readable by scripts, so guard
  against XSS
- Avoid passing the token in URLs

### 3. Client IP and Reverse Proxies

The "client IP" used by `ip_binding` and for audit logging **trusts only the directly connected
peer address by default**; `X-Forwarded-For` and `X-Real-IP` are ignored, because anyone can send
those headers.

When deploying behind a reverse proxy, put the proxy's address into `trusted_proxies`:

```python
config = SessionConfig(
    secret_key="...",
    ip_binding=True,
    trusted_proxies=["10.0.0.8"],  # the hop that connects directly
)
```

Entries can be single addresses or CIDR ranges, for load balancers that connect from a subnet:

```python
config = SessionConfig(
    secret_key="...",
    ip_binding=True,
    trusted_proxies=["10.0.0.0/8", "2001:db8::/32"],
)
```

The client address is then the **rightmost `X-Forwarded-For` entry that is not listed in
`trusted_proxies`**: proxies append to the header, so the leftmost entry is whatever the caller
chose to send and cannot be trusted. When the header arrives on several lines, they are read as
one comma-separated chain. If every entry in the chain is a trusted proxy, the direct
peer address is used. `X-Real-IP` is written by the proxy itself and has no chain to walk, so it is
used only when `X-Forwarded-For` yields no usable value.

> [!NOTE]
> An IPv4 peer reported in IPv4-mapped form (`::ffff:10.0.0.8`, as dual-stack sockets do) matches
> IPv4 entries. Entries that are not IP addresses (for example TestClient's `testclient`) match
> only an identical peer string, and an entry containing `/` that is not a valid CIDR range is
> rejected when the config is created.

The middleware applies this logic when it checks a binding, but `create_session()` binds whatever
`ip_address` you pass it. Behind a trusted proxy, `request.client.host` is the proxy's address,
which never matches, so the binding check fails on the next request. Pass the address the
middleware derives instead, either through the `ClientIPDep` dependency or by calling
`get_client_ip()` with the same config:

```python
from fastapi_cachex.session import get_client_ip
from fastapi_cachex.session.dependencies import ClientIPDep, SessionManagerDep


@app.post("/login")
async def login(manager: SessionManagerDep, client_ip: ClientIPDep):
    session, token = await manager.create_session(user, ip_address=client_ip)
    return {"token": token}


# Outside a route, with the SessionConfig you gave the middleware:
client_ip = get_client_ip(request, config)
```

### 4. IP Binding (Optional)

Improves security but can hurt the user experience (for example when the client's IP changes):

```python
config = SessionConfig(
    secret_key="...",
    ip_binding=True,  # bind the session to the client IP
)
```

The binding is recorded when the session is created, from the `ip_address` passed to
`create_session()` (or `user_agent` for `user_agent_binding`). If the value is missing at creation
time, a warning is logged and the session is created unbound. A request whose address does not
match the bound one (or has no address) is treated as having no session.

### 5. Regenerate the Session ID After Login

Prevents session fixation. The token a client arrives with may have been planted by
someone else (from a sibling subdomain, say); a login that keeps it hands that person a
logged-in session. Under `FastAPICacheXSessionMiddleware`, `login()` gives the session a new ID
and attaches the user in one call:

```python
from fastapi import Request

from fastapi_cachex.session import SessionUser, login


# Credentials is the body model from Basic Usage
@app.post("/login")
async def log_in(credentials: Credentials, request: Request):
    ...  # verify credentials.password
    await login(request, SessionUser(user_id=credentials.username))
    return {"ok": True}
```

What happens to the session the request arrived with depends on whose it is:

- **Anonymous** (a visitor's cart, say): it keeps its data under a new ID and gets the user.
- **The same `user_id`** (a re-login): the same, and the `SessionUser` you pass replaces the
  stored one, so changed roles or metadata take effect.
- **A different user's**: it is deleted, along with anything written to `request.session`
  earlier in the request, and `login()` starts a new session. None of the previous user's data
  (a cart, an `elevated` flag) reaches the new user.
- **None** (a new visitor, or a token that did not resolve): `login()` creates a session with
  the user, bound to the client IP and User-Agent as configured.

In every case the old token no longer resolves. The middleware then saves the session, keys
written to `request.session` after the call included (and before it, unless the loaded session
was a different user's), and sends its token through the transport the request used: the response header for a header or `Authorization: Bearer` token, otherwise
an HttpOnly `Set-Cookie` with every `cookie_*` attribute. Like every response that carries a
token, it gets `Cache-Control: private, no-store`. A later request with that token passes
`require_user_session` and `AuthenticatedSession`. `login()` returns the session, which
`get_session` also returns for the rest of the request.

A request that carried no token at all gets only the cookie, which page scripts cannot read. Do
not copy the token into a response header or the body of a browser login. An API client that
logs in without a token needs it in the body: return `manager.issue_token(session)` for the
session `login()` returned, or issue the token from a separate endpoint, as
[`examples/session_jwt.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt.py) does. The complete browser version is
[`examples/session_login.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_login.py).

Within one request, `request.session.clear()` after `login()` is a logout: the new session is
deleted and no token is sent (a cookie client gets its cookie expired). `clear()` before
`login()` logs the loaded session out, and `login()` then starts a new session instead of
rotating it. Without `FastAPICacheXSessionMiddleware`, `login()` raises `RuntimeError`: the
deprecated `SessionMiddleware` cannot send a token for a session it did not load, so there
create the session with `create_session(user=...)` and return its token.

`request.session["user_id"] = "123"` is not a login. It is application data, which
`require_user_session` and `AuthenticatedSession` do not recognise, and it keeps the session ID
the request arrived with.

To change the ID without logging in (after a privilege change, say), call
`await rotate_session_id(request)`:

```python
from fastapi_cachex.session import rotate_session_id
from fastapi_cachex.session.dependencies import AuthenticatedSession


@app.post("/sudo")
async def sudo(request: Request, session: AuthenticatedSession):
    ...  # check the password again
    await rotate_session_id(request)
    request.session["elevated"] = True
    return {"ok": True}
```

`rotate_session_id()` calls `SessionManager.regenerate_session_id()` on the request's
session, which deletes the backend record under the old ID and saves the session under a
new ID, keeping its data, user, `created_at` and expiry. Either middleware sees the new ID
and sends a token for it through the transport the request used: `Set-Cookie` for a
cookie, the response header for a header token. After that the old token no longer
resolves to a session. For a new visitor there is no session to rotate, so it returns
`False`.

A handler that already holds the request's session object can call
`await manager.regenerate_session_id(session)` directly, with the same effect. Get it from
`get_optional_session` and skip the call when it is `None`; `SessionDep` answers `401` to a
visitor who has no session yet.

Outside a middleware, load the session with the same bindings the middleware would pass, and hand
the returned token to the client yourself:

```python
session, _ = await manager.get_session(
    current_token, ip_address=client_ip, user_agent=user_agent
)
session, new_token = await manager.regenerate_session_id(session)
```

## SessionManager at a glance

`SessionManager(backend, config, token_serializer=None)` handles the whole
lifecycle: `create_session()` / `create_anonymous_session()` return
`(session, token)`; `get_session()` returns `(session, renewed_token)`, where
`renewed_token` is set only when sliding expiration renewed the token and should
be sent back to the client. `get_session()` raises a `SessionError` subclass on
failure: `SessionTokenError` (malformed token; for a JWT also a bad signature,
an expired `exp` or a wrong `iss`/`aud`), `SessionSecurityError` (bad `simple`
signature or binding mismatch), `SessionNotFoundError`, `SessionInvalidError`
(session not active) or `SessionExpiredError` (TTL or absolute timeout exceeded).
Since 0.3.8, `SessionError` derives from `CacheXError`, so `except CacheXError`
catches session errors too.

`get_session()` writes to the backend only when sliding expiration renewed the session, so a
request that only reads its session costs a single backend read. The returned
session's `last_accessed` is the current time, but the stored value is updated
only when the session is next written (created, modified, renewed or
regenerated). Pass `touch=True` to save it on every lookup. Before 0.3.8 every
lookup saved the session.

Every method with its signature is in the generated
[Session API reference](api/session.md).

## Dependencies

```python
from fastapi_cachex.session import (
    get_session,  # authentication required (401 when there is no session)
    get_optional_session,  # optional authentication (None when there is no session)
    require_session,  # alias of get_session
    require_user_session,  # 401 also when the session has no user
    get_session_manager,  # the SessionManager registered by the middleware
    login,  # not a dependency: await it to log a user in under a new session ID
    rotate_session_id,  # not a dependency: await it for a new session ID
)

# Type annotations
from fastapi_cachex.session.dependencies import (
    OptionalSession,  # Session | None
    RequiredSession,  # Session
    SessionDep,  # Session
    UserSessionDep,  # Session; anonymous sessions pass too, see above
    AuthenticatedSession,  # Session with a user (require_user_session)
    SessionManagerDep,  # SessionManager
)
```

`get_session_manager` returns the manager the middleware stored on `app.state` when it handled
its first request; it responds with `500` if no session middleware has run yet. Using it avoids
importing the manager into your route modules. From 0.4.0 it resolves the manager through
`SessionManagerProxy` instead, so register it there with `SessionManagerProxy.set(manager)`:
until then, `get_session_manager` (and `SessionManagerDep`, `ClientIPDep` and
`rotate_session_id()`, which use it) emits a `FutureWarning` once per app when the proxy holds no
manager or a different one. See [Migrating to 0.4.0](MIGRATING_0_4.md#get-session-manager).

```python
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session.dependencies import SessionManagerDep


# Credentials is the body model from Basic Usage
@app.post("/login")
async def login(credentials: Credentials, manager: SessionManagerDep):
    ...  # verify credentials.password
    user = SessionUser(user_id=credentials.username)
    session, token = await manager.create_session(user=user)
    return {"token": token}
```

`get_session` and `get_optional_session` also declare an `HTTPBearer` security scheme
(`SessionBearer`), so Swagger UI shows an **Authorize** button; the token itself is still read by
the middleware.
