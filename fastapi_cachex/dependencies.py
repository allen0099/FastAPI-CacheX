"""FastAPI dependency injection utilities for cache control."""

from typing import Annotated

from fastapi import Depends

from .backends.base import BaseCacheBackend
from .manager import CacheManager
from .manager_proxy import CacheManagerProxy
from .proxy import get_backend_or_fallback


def get_cache_backend() -> BaseCacheBackend:
    """Dependency to get the current cache backend instance.

    With no backend configured this falls back to a `MemoryBackend` and
    registers it, the same way `@cache` and `AppCache` do. It used to raise
    `BackendNotFoundError` (a 500) until some `@cache` route had run and
    installed the fallback first.
    """
    return get_backend_or_fallback()


CacheBackend = Annotated[BaseCacheBackend, Depends(get_cache_backend)]


def get_app_cache() -> CacheManager:
    """Dependency to get the application CacheManager instance.

    Lazily creates and registers a default CacheManager (backed by
    BackendProxy) the first time it's requested, unless one was already
    set via CacheManagerProxy.set(...).

    With no backend configured this falls back to a `MemoryBackend` and
    registers it, the same way `@cache` does. Building the manager without one
    used to raise `BackendNotFoundError` out of its constructor, so whether the
    dependency worked depended on whether some `@cache` route had already run
    and installed the fallback first.
    """
    return CacheManagerProxy.get_or_create(_default_manager)


def _default_manager() -> CacheManager:
    return CacheManager(backend=get_backend_or_fallback())


AppCache = Annotated[CacheManager, Depends(get_app_cache)]
