"""Session configuration settings."""

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr

SameSitePolicy = Literal["lax", "strict", "none"]


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
        description="Fraction of TTL that must pass before sliding refresh (0.5 = refresh after half TTL)",
    )

    # Token settings
    token_format: Literal["simple", "jwt"] = Field(
        default="simple",
        description="Token serialization format: 'simple' (default) or 'jwt'",
    )
    header_name: str = Field(
        default="X-Session-Token",
        description="Custom header name for session token",
    )
    use_bearer_token: bool = Field(
        default=True,
        description="Whether to accept Authorization Bearer tokens",
    )
    token_source_priority: list[Literal["header", "bearer"]] = Field(
        default=["header", "bearer"],
        description="Priority order for token sources",
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
        description="Leeway in seconds for exp/nbf/iat validation",
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
    user_agent_binding: bool = Field(
        default=False,
        description="Whether to bind session to User-Agent",
    )

    # Backend settings
    backend_key_prefix: str = Field(
        default="session:",
        description="Prefix for session keys in backend storage",
    )

    # Cookie settings (StarletteSessionMiddleware only)
    cookie_name: str = Field(
        default="session",
        description="Name of the cookie used to store the session token "
        "(StarletteSessionMiddleware only)",
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
        default=False,
        description="Whether to set the Secure flag on the session cookie "
        "(cookie only sent over HTTPS)",
    )
    cookie_domain: str | None = Field(
        default=None,
        description="Domain attribute for the session cookie; None omits the "
        "Domain attribute",
    )
