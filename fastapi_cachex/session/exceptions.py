"""Session-related exceptions."""

from fastapi_cachex.exceptions import CacheXError


class SessionError(CacheXError):
    """Base exception for session errors.

    Derives from ``CacheXError`` since 0.3.8, like ``StateError``, so
    ``except CacheXError`` also catches session errors.
    """


class SessionNotFoundError(SessionError):
    """Raised when a session is not found."""


class SessionExpiredError(SessionError):
    """Raised when a session has expired."""


class SessionInvalidError(SessionError):
    """Raised when a session is invalid."""


class SessionSecurityError(SessionError):
    """Raised when a session fails security checks."""


class SessionTokenError(SessionError):
    """Raised when there's an issue with session token."""
