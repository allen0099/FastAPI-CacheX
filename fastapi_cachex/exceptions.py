"""Custom exception classes for FastAPI-CacheX."""

import warnings
from typing import TYPE_CHECKING


class CacheXError(Exception):
    """Base class for all exceptions in FastAPI-CacheX."""


class _CacheError(CacheXError):
    """Exception raised for cache-related errors.

    .. deprecated:: 0.3.8
        Never raised by FastAPI-CacheX. Catch :class:`CacheXError` instead.
        Will be removed in version 0.4.0.
    """


_CacheError.__name__ = _CacheError.__qualname__ = "CacheError"

if TYPE_CHECKING:
    CacheError = _CacheError


class BackendNotFoundError(CacheXError):
    """Exception raised when a cache backend is not found."""


class ProxyNotSetError(BackendNotFoundError):
    """Exception raised when a manager proxy has no instance set.

    Raised by ``CacheManagerProxy``, ``SessionManagerProxy`` and
    ``StateManagerProxy``. It subclasses ``BackendNotFoundError``, which these
    proxies raised before 0.3.8, so existing handlers keep catching it.
    """


class RequestNotFoundError(CacheXError):
    """Exception raised when a request is not found."""


class LockTimeoutError(CacheXError):
    """Exception raised when acquiring a lock times out."""


def __getattr__(name: str) -> type[CacheXError]:
    """Return the deprecated ``CacheError`` with a ``DeprecationWarning``."""
    if name == "CacheError":
        warnings.warn(
            "CacheError is deprecated and never raised, catch CacheXError instead. "
            "Will be removed in version 0.4.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _CacheError
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
