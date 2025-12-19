"""Custom exception classes for state management."""

from fastapi_cachex.exceptions import CacheXError


class StateError(CacheXError):
    """Base exception for state-related errors."""


class InvalidStateError(StateError):
    """Raised when a state is invalid or not found."""


class StateExpiredError(StateError):
    """Raised when a state has expired."""


class StateDataError(StateError):
    """Raised when state data parsing or format is invalid."""
