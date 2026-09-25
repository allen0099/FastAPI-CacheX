# Session Management Extension

FastAPI-CacheX Session Management provides complete user session handling, including signed
tokens, sliding expiration, and optional IP/User-Agent binding. Session contents always live in
the cache backend; the client only holds a single signed token.

**How the token travels depends on which middleware you install:**

| Middleware | Token source | Response side | Status |
|------------|--------------|---------------|--------|
| `FastAPICacheXSessionMiddleware` | Custom header (default `X-Session-Token`) / `Authorization: Bearer` / **cookie** (default name `session`) | Routed by source: a token that arrived in a header is returned in the response header; a token that arrived in a cookie (or a brand-new session) gets `Set-Cookie` | **Recommended** |
| `SessionMiddleware` | Custom header / `Authorization: Bearer`; **no cookie support** | A renewed token is sent back in the response header | Deprecated, **removed in 0.4.0** |

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

```python
from fastapi import Depends, FastAPI, HTTPException
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.session import (
    FastAPICacheXSessionMiddleware,
    SessionConfig,
    SessionManager,
    SessionUser,
    get_optional_session,
    get_session,
)

# Create the FastAPI application
app = FastAPI()

# Session configuration (API-first architecture: the client manages the token)
config = SessionConfig(
    secret_key="your-secret-key-min-32-chars-long!!!",  # at least 32 characters
    session_ttl=3600,  # 1 hour
)

# Set up the backend and the session manager
backend = MemoryBackend()
session_manager = SessionManager(backend, config)

# Add the session middleware (SessionMiddleware is deprecated and removed in 0.4.0)
app.add_middleware(
    FastAPICacheXSessionMiddleware,
    session_manager=session_manager,
    config=config,
)

# Alternatively, register the manager on the proxy instead of passing it in:
#
#     from fastapi_cachex.session import SessionManagerProxy
#
#     SessionManagerProxy.set(session_manager)
#     app.add_middleware(FastAPICacheXSessionMiddleware)  # picked up from the proxy
#
# When `config` is omitted, the middleware uses `session_manager.config`.


# Login endpoint
@app.post("/login")
async def login(username: str, password: str):
    # Authenticate the user (simplified here)
    if username != "admin" or password != "secret":
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Create the session
    user = SessionUser(
        user_id="123",
        username=username,
        roles=["admin"],
    )
    session, token = await session_manager.create_session(user=user)

    # Return the token for the client to store (localStorage/sessionStorage).
    # The client sends it on later requests in the Authorization or X-Session-Token header.
    return {"message": "Login successful", "token": token}


# Endpoint that requires authentication
@app.get("/profile")
async def get_profile(session=Depends(get_session)):
    """Requires a valid session."""
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
        "roles": session.user.roles,
    }


# Endpoint with optional authentication
@app.get("/public")
async def public_endpoint(session=Depends(get_optional_session)):
    """Accessible with or without a session."""
    if session and session.user:
        return {"message": f"Hello, {session.user.username}!"}
    return {"message": "Hello, guest!"}


# Logout endpoint
@app.post("/logout")
async def logout(session=Depends(get_session)):
    await session_manager.delete_session(session.session_id)
    return {"message": "Logged out"}
```

`get_session` (and its alias `require_session`) raises `401 Authentication required` with a
`WWW-Authenticate: Bearer` header when the request carries no valid session. A token that is
malformed, forged, expired, invalidated or fails a binding check is never an error at the
middleware level: the request simply proceeds without a session.

The session object the dependencies return is the backend `Session` model. A session created by
`FastAPICacheXSessionMiddleware` from `request.session` (see the Migration section below) is
anonymous, so `session.user` is `None`.

### 3. Full Example (Redis Backend)

```python
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.session import (
    FastAPICacheXSessionMiddleware,
    SessionConfig,
    SessionManager,
    SessionUser,
    get_session,
)
from fastapi_cachex.session.dependencies import ClientIPDep

app = FastAPI()

# Redis backend
backend = AsyncRedisCacheBackend(
    host="localhost",
    port=6379,
    db=0,
)

# Session configuration with security options
config = SessionConfig(
    secret_key="your-very-secret-key-at-least-32-characters-long!!",
    session_ttl=3600,
    sliding_expiration=True,
    sliding_threshold=0.5,
    ip_binding=True,  # enable IP binding
    user_agent_binding=False,  # UA binding (optional)
)

session_manager = SessionManager(backend, config)

app.add_middleware(
    FastAPICacheXSessionMiddleware,
    session_manager=session_manager,
    config=config,
)


@app.post("/api/auth/login")
async def login(username: str, password: str, request: Request, client_ip: ClientIPDep):
    # Authenticate the user (should query a database)
    if not authenticate_user(username, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Create the session
    user = SessionUser(
        user_id=get_user_id(username),
        username=username,
        email=f"{username}@example.com",
        roles=get_user_roles(username),
    )

    # Collect client information for the bindings. `client_ip` is the address
    # the middleware checks later, including behind trusted proxies.
    user_agent = request.headers.get("user-agent")

    session, token = await session_manager.create_session(
        user=user,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    # Add a flash message
    session.add_flash_message("Login successful!", "success")
    await session_manager.update_session(session)

    return {
        "message": "Login successful",
        "token": token,  # the client stores this token and sends it on later requests
        "user": {
            "username": user.username,
            "roles": user.roles,
        },
    }


@app.get("/api/user/profile")
async def get_user_profile(session=Depends(get_session)):
    """Return the user's profile (requires authentication)."""
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
        "email": session.user.email,
        "roles": session.user.roles,
        "session_created": session.created_at.isoformat(),
        "last_accessed": session.last_accessed.isoformat(),
    }


@app.post("/api/user/update")
async def update_user_profile(
    email: str,
    session=Depends(get_session),
):
    """Update the user's profile."""
    session.user.email = email
    session.data["last_updated"] = datetime.now(timezone.utc).isoformat()

    # Persist the updated session
    await session_manager.update_session(session)

    return {"message": "Profile updated"}


@app.get("/api/messages")
async def get_flash_messages(session=Depends(get_session)):
    """Return and clear the flash messages."""
    messages = session.get_flash_messages(clear=True)
    # Clearing only changes the in-memory object; save it so the messages
    # are not shown again on the next request.
    await session_manager.update_session(session)
    return {"messages": messages}


@app.post("/api/auth/logout")
async def logout(session=Depends(get_session)):
    """Log out."""
    await session_manager.delete_session(session.session_id)

    # The client should discard its stored token
    return {"message": "Logged out successfully"}


@app.post("/api/auth/logout-all")
async def logout_all_devices(session=Depends(get_session)):
    """Log out from all devices."""
    user_id = session.user.user_id
    count = await session_manager.delete_user_sessions(user_id)
    return {"message": f"Logged out from {count} devices"}


# Helper functions (illustrative only)
def authenticate_user(username: str, password: str) -> bool:
    # A real implementation queries the database and verifies the password hash
    return True


def get_user_id(username: str) -> str:
    # A real implementation reads this from the database
    return f"user_{username}"


def get_user_roles(username: str) -> list[str]:
    # A real implementation reads this from the database
    return ["user"] if username != "admin" else ["admin", "user"]
```

Changes made to a `Session` object inside a handler (flash messages, `session.data`,
`session.user`) are only persisted when you call `session_manager.update_session(session)`.

`delete_user_sessions()` and `clear_expired_sessions()` enumerate every key in the backend via
`get_all_keys()` and load each session under `backend_key_prefix`, so their cost grows with the
size of the backend. On the Memcached backend, which cannot enumerate keys, they find nothing and
return `0` (with a `RuntimeWarning` from the backend).

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
  by source too: for a token that arrived in a header, a renewed token is sent back in the same
  response header and no `Set-Cookie` is emitted; a token that arrived in a cookie (or a brand-new
  anonymous session) uses `Set-Cookie`.

Both middlewares put the loaded `Session` object into `request.state`, so the existing session
dependencies `get_session`, `get_optional_session` and `require_session` work under either
middleware without any changes:

```python
from fastapi import Depends
from fastapi_cachex.session import FastAPICacheXSessionMiddleware, get_session

app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=manager, config=config
)


@app.get("/me")
async def me(session=Depends(get_session)):
    return {"user_id": session.user.user_id}
```

### `request.session` with `FastAPICacheXSessionMiddleware`

`request.session` is a view of the backend session's `data` dict:

- Writing to `request.session` when no session was loaded creates a new **anonymous** session
  (`SessionManager.create_anonymous_session()`, with IP/User-Agent bindings applied as
  configured) and sends its token back through the request's transport.
- Modifying it on a loaded session saves the new contents to the backend via `update_session()`,
  replacing `Session.data` with the dict's contents.
- Clearing it (`request.session.clear()`) on a session that had data deletes the backend session;
  a cookie client also receives a `Set-Cookie` that expires the cookie.
- Any access to `request.session` adds `Vary: Cookie` to the response.

The cookie is always `HttpOnly`; `Secure`, `SameSite`, `Domain`, `Path` and `Max-Age` follow the
`cookie_*` settings.

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
    cookie_name="session",
    cookie_max_age=14
    * 24
    * 60
    * 60,  # None = no Max-Age (cookie ends with the browser session)
    cookie_path="/",
    cookie_same_site="lax",  # "lax" / "strict" / "none"
    cookie_https_only=False,  # True adds the Secure flag
    cookie_domain=None,  # None = no Domain attribute
)
```

Sessions expire after `session_ttl` seconds. With `sliding_expiration`, each request that finds
less than `session_ttl * sliding_threshold` seconds remaining extends the expiry to a full
`session_ttl` again and issues a renewed token, which the middleware sends back to the client
(response header or `Set-Cookie`, see the table above). Header/bearer clients should replace their
stored token when the response carries the `header_name` header. `absolute_timeout` ends the
session that many seconds after it was created, regardless of sliding renewals.

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

`secret_key` is stored as a `SecretStr` and must be at least 32 characters long. Load it from the
environment or a secret store rather than hard-coding it; changing it invalidates every token
issued so far.

### 2. HTTPS Only

Always transport tokens over HTTPS in production. For cookie clients, mark the cookie `Secure`:

```python
config = SessionConfig(
    secret_key="...",
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
chose to send and cannot be trusted. If every entry in the chain is a trusted proxy, the direct
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

Prevents session fixation attacks:

```python
# After a successful login
session, _renewed_token = await session_manager.get_session(current_token)
session, new_token = await session_manager.regenerate_session_id(session)
# Hand new_token to the client; the old token no longer resolves to a session.
```

`regenerate_session_id()` deletes the backend record under the old ID and saves the session under
a new ID, keeping its data, user, `created_at` and expiry.

## SessionManager at a glance

`SessionManager(backend, config, token_serializer=None)` handles the whole
lifecycle: `create_session()` / `create_anonymous_session()` return
`(session, token)`; `get_session()` returns `(session, renewed_token)`, where
`renewed_token` is set only when sliding expiration renewed the token and should
be sent back to the client. `get_session()` raises a `SessionError` subclass on
failure: `SessionTokenError` (malformed token), `SessionSecurityError` (bad
signature or binding mismatch), `SessionNotFoundError`, `SessionInvalidError`
(session not active) or `SessionExpiredError` (TTL or absolute timeout exceeded).

Every method with its signature is in the generated
[Session API reference](api/session.md).

## Dependencies

```python
from fastapi_cachex.session import (
    get_session,  # authentication required (401 when there is no session)
    get_optional_session,  # optional authentication (None when there is no session)
    require_session,  # alias of get_session
    get_session_manager,  # the SessionManager registered by the middleware
)

# Type annotations
from fastapi_cachex.session.dependencies import (
    OptionalSession,  # Session | None
    RequiredSession,  # Session
    SessionDep,  # Session
    SessionManagerDep,  # SessionManager
)
```

`get_session_manager` returns the manager the middleware stored on `app.state` when it handled
its first request; it responds with `500` if no session middleware has run yet. Using it avoids
importing the manager into your route modules:

```python
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session.dependencies import SessionManagerDep


@app.post("/login")
async def login(username: str, manager: SessionManagerDep):
    session, token = await manager.create_session(user=SessionUser(user_id=username))
    return {"token": token}
```

`get_session` and `get_optional_session` also declare an `HTTPBearer` security scheme
(`SessionBearer`), so Swagger UI shows an **Authorize** button; the token itself is still read by
the middleware.
