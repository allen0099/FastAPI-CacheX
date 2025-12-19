"""Backend proxy for managing cache backend instances."""

from .backends import BaseCacheBackend
from .exceptions import BackendNotFoundError

_default_backend: BaseCacheBackend | None = None


class BackendProxy:
    """FastAPI CacheX Proxy for backend management."""

    @staticmethod
    def get_backend() -> BaseCacheBackend:
        """Get the current cache backend instance.

        Returns:
            The current cache backend

        Raises:
            BackendNotFoundError: If no backend has been set
        """
        if _default_backend is None:
            msg = "Backend is not set. Please set the backend first."
            raise BackendNotFoundError(msg)

        return _default_backend

    @staticmethod
    def set_backend(backend: BaseCacheBackend | None) -> None:
        """Set the backend for caching.

        Args:
            backend: The backend to use for caching, or None to clear the current backend
        """
        global _default_backend
        _default_backend = backend
