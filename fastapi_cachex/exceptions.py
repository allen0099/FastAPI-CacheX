class CacheXError(Exception):
    """Base class for all exceptions in FastAPI-CacheX."""


class CacheError(CacheXError):
    """Exception raised for cache-related errors."""
