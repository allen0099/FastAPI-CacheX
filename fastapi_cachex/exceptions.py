"""Custom exception classes for FastAPI-CacheX."""


class CacheXError(Exception):
    """Base class for all exceptions in FastAPI-CacheX."""


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


class LockTimeoutError(CacheXError, TimeoutError):
    """Exception raised when acquiring a lock or waiting for stampede protection times out."""
