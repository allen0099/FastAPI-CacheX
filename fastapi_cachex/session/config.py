"""Session configuration settings."""

import ipaddress
import warnings
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from pydantic import field_validator
from pydantic import model_validator

SameSitePolicy = Literal["lax", "strict", "none"]

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# The default ``SessionConfig.header_name``; ``@cache(vary=[...])`` also hashes
# this header's value like ``Authorization`` and ``Cookie``.
DEFAULT_SESSION_HEADER_NAME = "X-Session-Token"


@lru_cache(maxsize=256)
def _parse_network(entry: str) -> IPNetwork | None:
    """Parse a trusted-proxy entry as an IP network, or None if it is not one."""
    try:
        return ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return None


@lru_cache(maxsize=1024)
def _parse_address(value: str) -> IPAddress | None:
    """Parse a peer or forwarded address as an IP address, or None if it is not one."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    # A dual-stack socket reports IPv4 peers as ::ffff:a.b.c.d.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


# Signing algorithms accepted for JWT session tokens. `none` is deliberately
# absent: an unsigned token would make every session forgeable.
JWT_ALGORITHMS = frozenset(
    {
        "HS256",
        "HS384",
        "HS512",
        "RS256",
        "RS384",
        "RS512",
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
        "EdDSA",
    }
)

# The algorithms the built-in `JWTTokenSerializer` can use: it signs and
# verifies with the single `secret_key` string. The asymmetric ones above need
# a private key to sign and a public key to verify, so they only work with a
# custom `token_serializer`.
JWT_HMAC_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class SessionConfig(BaseModel):
    """Session configuration settings."""

    model_config = ConfigDict(extra="forbid")

    # Session lifetime
    session_ttl: int = Field(
        default=3600,
        description="Session time-to-live in seconds (default: 1 hour)",
    )
    absolute_timeout: int | None = Field(
        default=None,
        description="Absolute session timeout in seconds (None = no absolute timeout)",
    )
    sliding_expiration: bool = Field(
        default=True,
        description="Whether to refresh session expiry on each access",
    )
    sliding_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Renew once less than this fraction of session_ttl remains (0.5 = renew in the second half of the TTL)",
    )

    # Token settings
    token_format: Literal["simple", "jwt"] = Field(
        default="simple",
        description="Token serialization format: 'simple' (default) or 'jwt'",
    )
    header_name: str = Field(
        default=DEFAULT_SESSION_HEADER_NAME,
        description="Custom header name for session token",
    )
    use_bearer_token: bool = Field(
        default=True,
        description="Whether to accept Authorization Bearer tokens (deprecated: "
        'leave "bearer" out of token_source_priority instead)',
    )
    token_source_priority: list[Literal["header", "bearer", "cookie"]] = Field(
        default=["header", "bearer"],
        description="Priority order for token sources. The session cookie is "
        "read after the header sources, whether or not the list names it; "
        '"cookie" may only be the last entry',
    )

    # JWT settings (used when token_format == 'jwt')
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm (default: HS256)",
    )
    jwt_issuer: str | None = Field(
        default=None,
        description="Expected JWT issuer (iss). If set, will be verified.",
    )
    jwt_audience: str | None = Field(
        default=None,
        description="Expected JWT audience (aud). If set, will be verified.",
    )
    jwt_leeway: int = Field(
        default=0,
        ge=0,
        description="Leeway in seconds for exp/iat validation (nbf is not "
        "issued or verified; see docs/JWT_CLAIMS.md)",
    )

    # Security settings
    secret_key: SecretStr = Field(
        ...,
        min_length=32,
        description="Secret key for signing session tokens (min 32 characters)",
    )
    ip_binding: bool = Field(
        default=False,
        description="Whether to bind session to client IP address",
    )
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description="Peer addresses whose X-Forwarded-For / X-Real-IP headers "
        "may be believed. Empty (the default) ignores those headers and uses "
        "the direct peer address, since anyone can send them. When the peer is "
        "trusted, the client address is the rightmost X-Forwarded-For entry "
        "that is not itself listed here: proxies append, so the leftmost entry "
        "is whatever the caller chose to send. Entries may be IP addresses, "
        "CIDR ranges (e.g. 10.0.0.0/8) or other strings, which match exactly.",
    )
    user_agent_binding: bool = Field(
        default=False,
        description="Whether to bind session to User-Agent",
    )

    # Backend settings
    backend_key_prefix: str = Field(
        default="session:",
        description="Prefix for session keys in backend storage",
    )

    # Cookie settings (FastAPICacheXSessionMiddleware only)
    cookie_name: str = Field(
        default="__Host-session",
        description="Name of the cookie used to store the session token "
        "(FastAPICacheXSessionMiddleware only)",
    )
    cookie_max_age: int | None = Field(
        default=14 * 24 * 60 * 60,
        description="Max-Age (seconds) for the session cookie; None disables "
        "Max-Age/Expires (session cookie deleted when browser closes)",
    )
    cookie_path: str = Field(
        default="/",
        description="Path attribute for the session cookie",
    )
    cookie_same_site: SameSitePolicy = Field(
        default="lax",
        description="SameSite attribute for the session cookie",
    )
    cookie_https_only: bool = Field(
        default=True,
        description="Whether to set the Secure flag on the session cookie "
        "(cookie only sent over HTTPS)",
    )
    cookie_domain: str | None = Field(
        default=None,
        description="Domain attribute for the session cookie; None omits the "
        "Domain attribute",
    )

    @field_validator("trusted_proxies")
    @classmethod
    def _check_trusted_proxies(cls, value: list[str]) -> list[str]:
        """Reject entries written as a CIDR range that do not parse as one."""
        for entry in value:
            if "/" in entry and _parse_network(entry) is None:
                msg = f"trusted_proxies entry is not a valid CIDR range: {entry!r}"
                raise ValueError(msg)
        return value

    def is_trusted_proxy(self, address: str) -> bool:
        """Report whether `address` matches an entry of `trusted_proxies`.

        An IP address matches an entry that is the same address or a CIDR range
        containing it (IPv4-mapped IPv6 addresses count as their IPv4 form).
        Anything else, such as TestClient's ``testclient`` peer, matches only
        an identical entry.

        Args:
            address: Peer or forwarded address to check

        Returns:
            True if the address is a trusted proxy
        """
        if address in self.trusted_proxies:
            return True
        parsed = _parse_address(address)
        if parsed is None:
            return False
        for entry in self.trusted_proxies:
            network = _parse_network(entry)
            if network is not None and parsed in network:
                return True
        return False

    @model_validator(mode="after")
    def _warn_insecure_same_site_none(self) -> "SessionConfig":
        """Warn about a SameSite=None cookie without the Secure flag.

        Browsers drop such a cookie, so the session would silently never stick.
        Rejecting the combination would break existing configurations. A
        ``__Host-`` / ``__Secure-`` name without Secure is rejected by
        ``_check_cookie_prefix`` instead, so it is not warned about twice.
        """
        if (
            self.cookie_same_site == "none"
            and not self.cookie_https_only
            and not self.cookie_name.startswith(("__Host-", "__Secure-"))
        ):
            warnings.warn(
                'cookie_same_site="none" requires cookie_https_only=True: browsers '
                "reject a SameSite=None cookie without the Secure flag, so the "
                "session cookie would never be stored.",
                UserWarning,
                stacklevel=3,
            )
        return self

    @model_validator(mode="after")
    def _check_cookie_prefix(self) -> "SessionConfig":
        """Reject a ``__Host-`` / ``__Secure-`` cookie browsers would refuse.

        Browsers store a ``__Secure-`` cookie only with the Secure flag, and a
        ``__Host-`` cookie only with Secure, ``Path=/`` and no ``Domain``, so
        the session would silently never stick (#256).
        """
        problems: list[str] = []
        if self.cookie_name.startswith(("__Host-", "__Secure-")):
            if not self.cookie_https_only:
                problems.append("cookie_https_only=True")
            if self.cookie_name.startswith("__Host-"):
                if self.cookie_path != "/":
                    problems.append('cookie_path="/"')
                if self.cookie_domain is not None:
                    problems.append("cookie_domain=None")
        if problems:
            name = f"cookie_name={self.cookie_name!r}"
            if "cookie_name" not in self.model_fields_set:
                name += " (the default)"
            hints: list[str] = []
            if "cookie_https_only=True" in problems:
                hints.append(
                    "For plain-HTTP development, use a name without the prefix, "
                    "such as cookie_name='session' with cookie_https_only=False."
                )
            if self.cookie_path != "/" or self.cookie_domain is not None:
                hints.append(
                    "To set a cookie_path or cookie_domain, use "
                    "cookie_name='__Secure-session', which keeps the Secure flag."
                )
            msg = (
                f"{name} requires {', '.join(problems)}: browsers refuse a cookie "
                "with this prefix otherwise, so the session cookie would never be "
                f"stored. {' '.join(hints)} See "
                "https://fastapi-cachex.readthedocs.io/en/stable/SESSION/#cookie-defaults"
            )
            raise ValueError(msg)
        return self

    @field_validator("token_source_priority")
    @classmethod
    def _check_cookie_is_last(
        cls, value: list[Literal["header", "bearer", "cookie"]]
    ) -> list[Literal["header", "bearer", "cookie"]]:
        """Only accept ``"cookie"`` where the cookie is read: last.

        ``FastAPICacheXSessionMiddleware`` reads the cookie after the header
        sources whether or not the list names it, so listing it anywhere else
        would promise an order the middleware does not follow.
        """
        if "cookie" in value and value.index("cookie") != len(value) - 1:
            msg = (
                '"cookie" must be the last entry of token_source_priority: the '
                "session cookie is read after the header sources"
            )
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _warn_use_bearer_token(self) -> "SessionConfig":
        """Deprecate ``use_bearer_token``, removed with the session package in 0.5.0.

        ``token_source_priority`` already decides whether bearer tokens are
        read, so passing the flag at all warns, whatever its value.
        """
        if "use_bearer_token" in self.model_fields_set:
            warnings.warn(
                "SessionConfig(use_bearer_token=...) is deprecated and will be "
                "removed in version 0.5.0 with fastapi_cachex.session. "
                "token_source_priority decides the "
                "token sources: drop use_bearer_token=True (the default), and "
                'replace use_bearer_token=False by leaving "bearer" out of '
                'token_source_priority, keeping "cookie" last, e.g. '
                '["header", "cookie"] '
                "(https://github.com/allen0099/FastAPI-CacheX/issues/377).",
                DeprecationWarning,
                stacklevel=3,
            )
        return self

    @field_validator("jwt_algorithm")
    @classmethod
    def _check_jwt_algorithm(cls, value: str) -> str:
        """Reject signing algorithms that are unsupported or unsafe."""
        if value not in JWT_ALGORITHMS:
            supported = ", ".join(sorted(JWT_ALGORITHMS))
            msg = f"jwt_algorithm must be one of: {supported}"
            raise ValueError(msg)
        return value
