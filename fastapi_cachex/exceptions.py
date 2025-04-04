class CacheXError(Exception):
    """Base class for all exceptions in FastAPI-CacheX."""


class CacheError(CacheXError):
    """Exception raised for cache-related errors."""


class BackendNotFoundError(CacheXError):
    """Exception raised when a cache backend is not found."""


class RequestNotFoundError(CacheXError):
    """Exception raised when a request is not found."""
