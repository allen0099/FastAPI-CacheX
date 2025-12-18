"""Session-related exceptions."""


class SessionError(Exception):
    """Base exception for session errors."""


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
