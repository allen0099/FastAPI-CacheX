# JWT Claims: Implementation Notes and Extension Guide

> [!WARNING]
> **Deprecated.** `fastapi_cachex.session` is deprecated in 0.4.0 and removed in 0.5.0 ([#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)). Importing it emits a `FutureWarning`. [Migrating to 0.4.0](MIGRATING_0_4.md#session-state-deprecated) says where to move.

## Overview

FastAPI-CacheX's JWT token serializer implements a minimal set of JWT claims to carry session tokens securely. This document explains:

1. Why we do not implement the full set of JWT claims (such as `jti` and `nbf`)
2. The design considerations behind the current implementation
3. How to extend the serializer with custom claims

The implementation lives in [`fastapi_cachex/session/token_serializers.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/session/token_serializers.py). JWT support requires the optional extra: `pip install "fastapi-cachex[jwt]"`.

## JWT Claims in the Current Implementation

### Implemented Standard Claims

`JWTTokenSerializer` implements the following JWT claims:

| Claim | Name | Required | Verified | Description |
|-------|------|----------|----------|-------------|
| `sid` | Session ID | ✅ | ✅ | Custom claim that maps to the server-side session |
| `iat` | Issued At | ✅ | ✅ | Time the token was issued (RFC 7519); rejected if it lies in the future (beyond `jwt_leeway`) |
| `exp` | Expiration | ✅ | ✅ | Token expiry: the session's `expires_at` (so it follows sliding expiration and never passes `absolute_timeout`), falling back to `iat + session_ttl` |
| `iss` | Issuer | ⚠️ | ✅ | Token issuer (optional; only issued and verified when `jwt_issuer` is set) |
| `aud` | Audience | ⚠️ | ✅ | Intended audience (optional; only issued and verified when `jwt_audience` is set) |

### Related `SessionConfig` Fields

| Field | Default | Description |
|-------|---------|-------------|
| `token_format` | `"simple"` | Set to `"jwt"` to use `JWTTokenSerializer` |
| `secret_key` | (required) | Signing key, at least 32 characters; used both to sign and to verify the JWT |
| `jwt_algorithm` | `"HS256"` | Signing algorithm; must be one of the supported values (`none` is rejected) |
| `jwt_issuer` | `None` | Expected `iss`; issued and verified when set |
| `jwt_audience` | `None` | Expected `aud`; issued and verified when set |
| `jwt_leeway` | `0` | Leeway in seconds for `exp`/`iat` validation |
| `session_ttl` | `3600` | Session lifetime in seconds; used for `exp` when the session has no `expires_at` |

> [!NOTE]
> **Asymmetric algorithms:** `jwt_algorithm` accepts `HS*`, `RS*`, `ES*`, `PS*` and `EdDSA`, but the built-in serializer signs and verifies with the single `secret_key` string, so it only supports the HMAC algorithms (`HS256`, `HS384`, `HS512`). Building a `SessionManager` with an asymmetric algorithm and no custom serializer raises `ValueError`. To use one, pass a custom `token_serializer` that encodes with a private key and decodes with the matching public key (see [Extension Guide](#extension-guide-adding-custom-claims)).

### Standard Claims That Are Not Implemented

The following optional claims defined by RFC 7519 are **not implemented**:

| Claim | Name | Purpose | Why it is not implemented |
|-------|------|---------|---------------------------|
| `jti` | JWT ID | Unique token identifier, prevents replay attacks | The stateful session model already handles this through server-side state |
| `nbf` | Not Before | Time the token becomes valid | Sessions normally take effect immediately; no delayed activation is needed |
| `sub` | Subject | Subject identifier (usually the user ID) | A custom `sid` claim is clearer for representing a session ID |

## Design Rationale

### Stateful Session vs Stateless JWT

FastAPI-CacheX uses a **stateful session** model, which is fundamentally different from a purely stateless JWT:

```
┌─────────────────────────────────────────────────────────┐
│  FastAPI-CacheX Session Model (Stateful)                │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────┐         ┌──────────┐        ┌──────────┐  │
│  │  Client  │  JWT    │  Server  │        │  Redis/  │  │
│  │          │ ──────> │          │ ────>  │  Cache   │  │
│  │          │  (sid)  │          │ lookup │          │  │
│  └──────────┘         └──────────┘        └──────────┘  │
│                                                         │
│  The JWT carries only the session ID (sid)              │
│  The actual session data is stored server-side          │
│  Revocable instantly (delete the session from cache)    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Traditional Stateless JWT (NOT used by CacheX)         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────┐         ┌──────────┐                      │
│  │  Client  │  JWT    │  Server  │                      │
│  │          │ ──────> │          │                      │
│  │          │ (all)   │          │                      │
│  └──────────┘         └──────────┘                      │
│                                                         │
│  The JWT contains all user info and permissions         │
│  The server is stateless and cannot revoke tokens       │
│  Revocation requires jti + a blacklist                  │
└─────────────────────────────────────────────────────────┘
```

A valid JWT signature is necessary but not sufficient: after decoding, `SessionManager.get_session()` still loads the session from the backend and rejects it if it is missing, not active, expired, past `absolute_timeout`, or fails IP/User-Agent binding checks. Any decoding failure is raised as `SessionTokenError`, which the session middleware (`FastAPICacheXSessionMiddleware`) treats as "no session".

### Why a Stateful Session

#### ✅ Advantages

1. **Instant revocation**
    - `SessionManager.delete_session()` (or `invalidate_session()`) takes effect immediately
    - No token blacklist to maintain
    - No need for a `jti` claim and a blacklist system

2. **Protection of sensitive data**
    - Session data (including user info) is stored server-side
    - The JWT contains only minimal information (the session ID)
    - Reduces the impact of a leaked JWT

3. **Flexible session management**
    - Supports sliding expiration: when a session is renewed, the middleware sends a new token with an updated `exp` through the transport the request used: the response header named by `header_name` (`X-Session-Token` by default), or `Set-Cookie` for a cookie
    - Supports updating session data in real time
    - Supports features such as flash messages

4. **Small tokens**
    - The JWT only needs to carry `sid` and timestamps
    - Less network overhead
    - Well suited to the frequent requests of API-first architectures

#### ⚠️ Trade-offs

1. **Requires backend storage**
    - Needs a Redis, Memcached, or Memory backend
    - Horizontal scaling requires a shared cache (such as a Redis cluster)

2. **Every request needs a cache lookup**
    - Adds one cache lookup per request
    - But modern cache systems (Redis) are very fast (sub-millisecond)

### Why Some Claims Are Not Needed

#### `jti` (JWT ID)

**Purpose**: generate a unique ID for every JWT, used for:

- Token blacklists
- Preventing token replay attacks
- Tracking individual tokens

**Why it is not needed**:

```python
# A stateless JWT needs jti + a blacklist
jwt_payload = {"jti": "uuid-1234", "user_id": "123", ...}
# To revoke: add the jti to a blacklist and check it on every verification

# FastAPI-CacheX stateful session
jwt_payload = {"sid": "session-abc123"}
# To revoke: delete the session from the cache directly
await session_manager.delete_session("session-abc123")
# On the next request the cache lookup fails and the request is rejected automatically
```

#### `nbf` (Not Before)

**Purpose**: specify when a token becomes valid, used for:

- Issuing tokens in advance for future use
- Tolerating clock skew

**Why it is not needed**:

- A session normally takes effect as soon as it is created
- If delayed activation is required, it belongs in the application logic
- The `jwt_leeway` setting already handles clock skew for `exp` and `iat`

#### `sub` (Subject)

**Purpose**: identify the subject of the token (usually the user ID)

**Why `sid` is used instead**:

- `sub` usually denotes an **immutable** user identifier
- `sid` denotes a **mutable** session identifier
- `SessionManager.regenerate_session_id()` changes `sid`, while `user_id` stays the same
- `sid` makes the semantics clearer

## Extension Guide: Adding Custom Claims

If your application needs additional JWT claims, write your own serializer and pass an instance to `SessionManager` through its `token_serializer` argument. Any object with `to_string(token) -> str` and `from_string(token_str) -> SessionToken` methods (the `TokenSerializer` protocol) will do; `from_string()` should raise `ValueError` for invalid tokens, which `SessionManager` converts into `SessionTokenError`.

The base class below does what the built-in `JWTTokenSerializer` does and leaves two hooks for the extra claims. It keeps its own copy of the settings, read from the public `SessionConfig` fields, instead of reaching into `JWTTokenSerializer`'s private attributes, which may change in any release. Like the built-in serializer, it follows `token.expires_at` in `to_string()`, so `exp` keeps up with sliding expiration. It is the `serializer` part of [`examples/session_jwt_claims.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt_claims.py).

<!-- fmt:off -->
```python
--8<-- "examples/session_jwt_claims.py:serializer"
```
<!-- fmt:on -->

PyJWT verifies the signature, `exp`, `iat` and (when present) `nbf` by default, and `iss`/`aud` when `issuer`/`audience` are given. Two checks of the built-in serializer are not repeated here: it rejects an asymmetric `jwt_algorithm` and rejects (`ValueError`) a `secret_key` shorter than the HMAC output. This class signs with `secret_key` too, so keep an `HS*` algorithm; for an asymmetric one, hold the private and public keys in the class and use them in `jwt.encode()` and `jwt.decode()`.

### Example 1: Adding `jti` and `nbf`

```python
import uuid
from typing import Any

from fastapi_cachex.session.models import SessionToken


class ExtendedJWTSerializer(CustomClaimsJWTSerializer):
    """Adds the jti and nbf claims."""

    required_claims = ("jti", "nbf")

    def extra_claims(self, token: SessionToken) -> dict[str, Any]:
        return {
            "jti": str(uuid.uuid4()),  # Unique token ID
            "nbf": int(token.issued_at.timestamp()),  # Not before = issued at
        }
```

### Example 2: Adding Multi-Tenant Custom Claims

From [`examples/session_jwt_claims.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt_claims.py):

<!-- fmt:off -->
```python
--8<-- "examples/session_jwt_claims.py:multi-tenant"
```
<!-- fmt:on -->

### Using a Custom Serializer

#### Option 1: Pass it to `SessionManager` (recommended)

From [`examples/session_jwt_claims.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt_claims.py):

<!-- fmt:off -->
```python
--8<-- "examples/session_jwt_claims.py:setup"
```
<!-- fmt:on -->

When `token_serializer` is given it overrides the built-in choice made from `token_format`. Keep `token_format="jwt"` anyway: with `"simple"`, `SessionManager` additionally performs its own HMAC signature check on the parsed token, which a JWT-based serializer does not provide.

The example uses `MemoryBackend` so it runs without a server; any backend works, for example the Redis setup of [Backends](BACKENDS.md#closing-a-backend).

#### Option 2: Subclass `SessionManager` (advanced)

```python
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.session import SessionConfig, SessionManager


class MultiTenantSessionManager(SessionManager):
    """SessionManager with multi-tenant support."""

    def __init__(
        self, backend: BaseCacheBackend, config: SessionConfig, tenant_id: str
    ) -> None:
        super().__init__(
            backend,
            config,
            token_serializer=MultiTenantJWTSerializer(
                config=config, tenant_id=tenant_id
            ),
        )


# Usage
manager = MultiTenantSessionManager(backend, config, tenant_id="acme-corp")
```

Do not replace the serializer by assigning a private attribute after construction; pass it through the `token_serializer` argument so the manager uses it for both issuing and parsing tokens.

## Complete Application Example

[`examples/session_jwt_claims.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt_claims.py) puts the pieces above together into a runnable app:

<!-- fmt:off -->
```python
--8<-- "examples/session_jwt_claims.py"
```
<!-- fmt:on -->

## Security Considerations

### 1. Token Size

Adding more claims increases the size of the JWT, which affects:

- Network overhead
- Cookie size limits (if the token is stored in a cookie, e.g. with `FastAPICacheXSessionMiddleware`)
- Performance

**Recommendation**: add only the claims you need and avoid putting large amounts of data in the JWT.

### 2. Sensitive Data

Do not store sensitive data (such as passwords or credit card numbers) in a JWT:

- A JWT can be decoded (it is base64url-encoded)
- Even with a signature, the contents are readable
- Store sensitive data in the server-side session instead

### 3. Claim Validation

Always validate custom claims in `from_string()`:

```python
# ❌ Bad: no validation
payload = jwt.decode(token_str, self.secret, algorithms=[self.algorithm])
tenant_id = payload.get("tenant_id")  # May be missing or invalid

# ✅ Good: strict validation
payload = jwt.decode(
    token_str,
    self.secret,
    algorithms=[self.algorithm],
    options={"require": ["sid", "iat", "exp", "tenant_id"]},
)
if payload["tenant_id"] != self.tenant_id:
    raise ValueError("Invalid tenant_id")
```

### 4. Key Rotation

To support key rotation, you can use the `kid` (Key ID) header parameter. The following is a sketch built on `CustomClaimsJWTSerializer`; building `payload` and `kwargs` works as in its `to_string()` and `from_string()`:

```python
class KeyRotationJWTSerializer(CustomClaimsJWTSerializer):
    def __init__(
        self, config: SessionConfig, keys: dict[str, str], current_key_id: str
    ) -> None:
        super().__init__(config)
        # Key ID -> secret. Keep a retired key until its tokens have expired.
        self.keys = keys
        self.current_key_id = current_key_id

    def to_string(self, token: SessionToken) -> str:
        # Sign with the current key and name it in the header
        return jwt.encode(
            payload,
            self.keys[self.current_key_id],
            algorithm=self.algorithm,
            headers={"kid": self.current_key_id},
        )

    def from_string(self, token_str: str) -> SessionToken:
        # Read kid from the (not yet verified) header and pick the matching key
        kid = jwt.get_unverified_header(token_str).get("kid")
        key = self.keys.get(kid)
        if key is None:
            msg = "Unknown key ID"
            raise ValueError(msg)

        payload = jwt.decode(token_str, key, algorithms=[self.algorithm], **kwargs)
        # ...
```

## Testing Recommendations

Add tests for your custom serializer:

```python
import jwt
import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import SessionConfig, SessionManager, SessionUser
from fastapi_cachex.session.exceptions import SessionTokenError


@pytest.mark.asyncio
async def test_custom_claims_included():
    """Custom claims are included in the JWT and the token round-trips."""
    backend = MemoryBackend()
    config = SessionConfig(secret_key="a" * 32, token_format="jwt")

    serializer = MultiTenantJWTSerializer(
        config=config,
        tenant_id="test-tenant",
        api_version="v1",
    )
    manager = SessionManager(backend, config, token_serializer=serializer)

    user = SessionUser(user_id="u1", username="alice")
    session, token = await manager.create_session(user=user)

    # The token can be decoded and carries the custom claim
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["tenant_id"] == "test-tenant"

    # get_session returns (session, renewed_token)
    retrieved, _renewed = await manager.get_session(token)
    assert retrieved.session_id == session.session_id


@pytest.mark.asyncio
async def test_custom_claims_validated():
    """A token whose custom claims fail validation is rejected."""
    backend = MemoryBackend()
    config = SessionConfig(secret_key="a" * 32, token_format="jwt")

    # Create a token with tenant_id="tenant-1"
    manager1 = SessionManager(
        backend,
        config,
        token_serializer=MultiTenantJWTSerializer(config, tenant_id="tenant-1"),
    )
    _session, token = await manager1.create_session(user=SessionUser(user_id="u1"))

    # Try to validate it with tenant_id="tenant-2" (must fail)
    manager2 = SessionManager(
        backend,
        config,
        token_serializer=MultiTenantJWTSerializer(config, tenant_id="tenant-2"),
    )

    # The serializer's ValueError surfaces as SessionTokenError
    with pytest.raises(SessionTokenError, match="Invalid tenant_id"):
        await manager2.get_session(token)
```

## FAQ

### Q: Why isn't `jti` implemented by default?

A: `jti` is mainly used to revoke stateless JWTs (via a blacklist). FastAPI-CacheX uses stateful sessions, so a token can be revoked by deleting the server-side session data directly; no separate blacklist mechanism is needed.

### Q: Do I need `nbf`?

A: In most cases, no. `nbf` is for tokens that are issued in advance but become valid later. If your application needs this, we recommend handling it in the application logic (for example, recording the activation time in `session.data`) rather than at the JWT level.

### Q: Can I add claims without writing code?

A: Not currently; custom claims require a custom `token_serializer`, such as the classes in the [Extension Guide](#extension-guide-adding-custom-claims). A future version might add a configuration option such as the hypothetical one below (it does not exist today, and `SessionConfig` rejects unknown fields):

```python
SessionConfig(
    token_format="jwt",
    jwt_custom_claims={"tenant_id": "acme", "version": "v1"},
)
```

That would add complexity, though. The current design offers enough flexibility while keeping the code simple.

### Q: Do custom claims affect performance?

A: Only slightly. JWT encoding/decoding performance depends mainly on:

1. The signing algorithm (HS256 is fast)
2. Token size (more claims = larger token)
3. Network transfer (larger tokens)

As long as you don't add large amounts of data, the impact is negligible.

### Q: How do I include user permissions in the JWT?

A: We don't recommend putting permissions in the JWT. FastAPI-CacheX uses stateful sessions, so you should:

```python
# ✅ Recommended: store them in the server-side session
session.user.roles = ["admin", "editor"]
session.user.permissions = ["read", "write", "delete"]
await manager.update_session(session)

# ❌ Not recommended: putting them in JWT claims
# Permission changes cannot take effect immediately unless every existing token is revoked
```

## References

- [RFC 7519 - JSON Web Token (JWT)](https://datatracker.ietf.org/doc/html/rfc7519)
- [PyJWT Documentation](https://pyjwt.readthedocs.io/)
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [FastAPI-CacheX Session Documentation](SESSION.md)

## Summary

FastAPI-CacheX's JWT implementation focuses on the **stateful session** use case and provides:

- ✅ **Implemented**: the basic JWT claims (`sid`, `iat`, `exp`, `iss`, `aud`)
- ✅ **Implemented**: signature verification and expiry checks
- ✅ **Implemented**: an extensible design (via subclassing and the `token_serializer` argument)
- ⚠️ **Not implemented**: `jti`, `nbf`, `sub` (these are not required for stateful sessions)
- 🔧 **Extensible**: developers can easily add custom claims (see the examples in this document)

This design strikes a good balance between security, performance, and flexibility. If your application has special requirements, refer to the extension examples in this document.
