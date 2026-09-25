"""FastAPI dependency injection utilities for cache control."""

import threading
from typing import Annotated

from fastapi import Depends

from .backends.base import BaseCacheBackend
from .exceptions import BackendNotFoundError
from .manager import CacheManager
from .manager_proxy import CacheManagerProxy
from .proxy import BackendProxy
from .proxy import get_backend_or_fallback

_manager_lock = threading.Lock()


def get_cache_backend() -> BaseCacheBackend:
    """Dependency to get the current cache backend instance."""
    return BackendProxy.get()


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
    try:
        return CacheManagerProxy.get()
    except BackendNotFoundError:
        pass
    # Checked again under the lock: FastAPI runs this sync dependency in a
    # worker thread, so concurrent first requests would otherwise each build
    # and register their own manager (and fallback backend).
    with _manager_lock:
        try:
            return CacheManagerProxy.get()
        except BackendNotFoundError:
            manager = CacheManager(backend=get_backend_or_fallback())
            CacheManagerProxy.set(manager)
            return manager


AppCache = Annotated[CacheManager, Depends(get_app_cache)]
