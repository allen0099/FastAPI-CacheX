"""FastAPI dependency injection utilities for cache control."""

from typing import Annotated

from fastapi import Depends

from .backends.base import BaseCacheBackend
from .proxy import BackendProxy


def get_cache_backend() -> BaseCacheBackend:
    """Dependency to get the current cache backend instance."""
    return BackendProxy.get()


CacheBackend = Annotated[BaseCacheBackend, Depends(get_cache_backend)]
