from fastapi_cachex.backends import BaseCacheBackend
from fastapi_cachex.exceptions import BackendNotFoundError

_default_backend: BaseCacheBackend | None = None


class BackendProxy:
    """FastAPI CacheX Proxy"""

    @staticmethod
    def get_backend() -> BaseCacheBackend:
        if _default_backend is None:
            raise BackendNotFoundError(
                "Backend is not set. Please set the backend first."
            )

        return _default_backend

    @staticmethod
    def set_backend(backend: BaseCacheBackend) -> None:
        """Set the backend for caching."""
        global _default_backend
        _default_backend = backend
