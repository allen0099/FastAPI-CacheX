"""FastAPI-CacheX: A powerful and flexible caching extension for FastAPI."""

import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

from .cache import build_cache_key as build_cache_key
from .cache import cache as cache
from .cache import default_key_builder as default_key_builder
from .cache import invalidate as invalidate
from .cache_key import CacheKey as CacheKey
from .cached import CachedFunction as CachedFunction
from .cached import cached as cached
from .dependencies import AppCache as AppCache
from .dependencies import CacheBackend as CacheBackend
from .dependencies import get_app_cache as get_app_cache
from .dependencies import get_cache_backend as get_cache_backend
from .exceptions import BackendNotFoundError as BackendNotFoundError
from .exceptions import CacheXError as CacheXError
from .exceptions import LockTimeoutError as LockTimeoutError
from .exceptions import ProxyNotSetError as ProxyNotSetError
from .exceptions import RequestNotFoundError as RequestNotFoundError
from .lock import CacheLock as CacheLock
from .manager import CacheManager as CacheManager
from .manager_proxy import CacheManagerProxy as CacheManagerProxy
from .proxy import BackendProxy as BackendProxy
from .routes import add_routes as add_routes
from .types import CacheKeyBuilder as CacheKeyBuilder


def _read_version() -> str:
    """Return the installed distribution's version.

    Importing from a source tree that was never installed leaves no metadata to
    read; reporting a development version there is part of the contract, so this
    lives in a function the tests can drive rather than behind a coverage pragma.
    """
    try:
        return version("fastapi-cachex")
    except PackageNotFoundError:
        return "0.0.0.dev0"


__version__ = _read_version()

_package_logger = logging.getLogger("fastapi_cachex")
_package_logger.addHandler(
    logging.NullHandler()
)  # Attach a NullHandler to avoid "No handler found" warnings in user applications.

__all__ = [
    "AppCache",
    "BackendNotFoundError",
    "BackendProxy",
    "CacheBackend",
    "CacheKey",
    "CacheKeyBuilder",
    "CacheLock",
    "CacheManager",
    "CacheManagerProxy",
    "CacheXError",
    "CachedFunction",
    "LockTimeoutError",
    "ProxyNotSetError",
    "RequestNotFoundError",
    "__version__",
    "add_routes",
    "build_cache_key",
    "cache",
    "cached",
    "default_key_builder",
    "get_app_cache",
    "get_cache_backend",
    "invalidate",
]
