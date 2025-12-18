"""Session configuration settings."""

from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class SessionConfig(BaseModel):
    """Session configuration settings."""

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

    # Cookie settings
    cookie_name: str = Field(
        default="fastapi_session", description="Session cookie name"
    )
    cookie_max_age: int | None = Field(
        default=None,
        description="Cookie max-age in seconds (None = session cookie)",
    )
    cookie_path: str = Field(default="/", description="Cookie path")
    cookie_domain: str | None = Field(default=None, description="Cookie domain")
    cookie_secure: bool = Field(
        default=True,
        description="Whether cookie should only be sent over HTTPS",
    )
    cookie_httponly: bool = Field(
        default=True,
        description="Whether cookie should be inaccessible to JavaScript",
    )
    cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        description="SameSite cookie attribute",
    )

    # Header settings
    header_name: str = Field(
        default="X-Session-Token",
        description="Custom header name for session token",
    )
    use_bearer_token: bool = Field(
        default=True,
        description="Whether to accept Authorization Bearer tokens",
    )
    token_source_priority: list[Literal["cookie", "header", "bearer"]] = Field(
        default=["cookie", "header", "bearer"],
        description="Priority order for token sources",
    )

    # Security settings
    secret_key: str = Field(
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
    regenerate_on_login: bool = Field(
        default=True,
        description="Whether to regenerate session ID on login",
    )

    # Backend settings
    backend_key_prefix: str = Field(
        default="session:",
        description="Prefix for session keys in backend storage",
    )

    # CSRF settings
    enable_csrf: bool = Field(
        default=False,
        description="Whether to enable CSRF protection",
    )
    csrf_cookie_name: str = Field(
        default="fastapi_csrf",
        description="CSRF token cookie name",
    )
    csrf_header_name: str = Field(
        default="X-CSRF-Token",
        description="CSRF token header name",
    )
