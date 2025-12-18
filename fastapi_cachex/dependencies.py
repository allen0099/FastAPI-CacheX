"""FastAPI dependency injection utilities for cache control."""

from typing import Annotated

from fastapi import Depends

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.proxy import BackendProxy


def get_cache_backend() -> BaseCacheBackend:
    """Dependency to get the current cache backend instance."""
    return BackendProxy.get_backend()


CacheBackend = Annotated[BaseCacheBackend, Depends(get_cache_backend)]
