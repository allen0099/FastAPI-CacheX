"""FastAPI dependency injection utilities for cache control."""

from typing import Annotated

from fastapi import Depends

from .backends.base import BaseCacheBackend
from .exceptions import BackendNotFoundError
from .manager import CacheManager
from .manager_proxy import CacheManagerProxy
from .proxy import BackendProxy


def get_cache_backend() -> BaseCacheBackend:
    """Dependency to get the current cache backend instance."""
    return BackendProxy.get()


CacheBackend = Annotated[BaseCacheBackend, Depends(get_cache_backend)]


def get_app_cache() -> CacheManager:
    """Dependency to get the application CacheManager instance.

    Lazily creates and registers a default CacheManager (backed by
    BackendProxy) the first time it's requested, unless one was already
    set via CacheManagerProxy.set(...).
    """
    try:
        return CacheManagerProxy.get()
    except BackendNotFoundError:
        manager = CacheManager()
        CacheManagerProxy.set(manager)
        return manager


AppCache = Annotated[CacheManager, Depends(get_app_cache)]
