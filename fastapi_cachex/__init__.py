"""FastAPI-CacheX: A powerful and flexible caching extension for FastAPI."""

import logging
import sys
import warnings
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

from ._vary import VaryNormaliser as VaryNormaliser
from .cache import build_cache_key as build_cache_key
from .cache import cache as cache
from .cache import default_key_builder as default_key_builder
from .cache import invalidate as invalidate
from .cache import invalidate_path as invalidate_path
from .cache_key import CacheKey as CacheKey
from .cached import CachedFunction as CachedFunction
from .cached import cached as cached
from .dependencies import AppCache as AppCache
from .dependencies import CacheBackend as CacheBackend
from .dependencies import get_app_cache as get_app_cache
from .dependencies import get_cache_backend as get_cache_backend
from .exceptions import BackendNotFoundError as BackendNotFoundError
from .exceptions import BackendUnavailableError as BackendUnavailableError
from .exceptions import CacheXError as CacheXError
from .exceptions import InvalidTTLError as InvalidTTLError
from .exceptions import LockTimeoutError as LockTimeoutError
from .exceptions import ProxyNotSetError as ProxyNotSetError
from .exceptions import RequestNotFoundError as RequestNotFoundError
from .exceptions import TTLTypeError as TTLTypeError
from .exceptions import TTLValueError as TTLValueError
from .exceptions import ValueTooLargeError as ValueTooLargeError
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

_PYTHON_310_NOTICE = (
    "fastapi-cachex 0.6.0 will require Python 3.11 or later; Python 3.10 "
    "reached its end of life in October 2026. Upgrade Python; see "
    "https://fastapi-cachex.readthedocs.io/en/stable/MIGRATING_0_6/#python-311"
)


def _warn_python_310(python: tuple[int, int] = sys.version_info[:2]) -> None:
    """Warn on import under Python 3.10, which 0.6.0 drops (#491).

    ``FutureWarning``, not ``DeprecationWarning``: it is shown by default.
    ``stacklevel=3`` skips this function and the package module; ``warnings``
    skips the import machinery's frames itself, so the warning names the line
    that imported ``fastapi_cachex`` (for ``importlib.import_module()`` it
    names ``importlib/__init__.py``; 3.10 has no ``skip_file_prefixes``).
    """
    if python < (3, 11):
        warnings.warn(_PYTHON_310_NOTICE, FutureWarning, stacklevel=3)


_warn_python_310()

_package_logger = logging.getLogger("fastapi_cachex")
_package_logger.addHandler(
    logging.NullHandler()
)  # Attach a NullHandler to avoid "No handler found" warnings in user applications.

__all__ = [
    "AppCache",
    "BackendNotFoundError",
    "BackendProxy",
    "BackendUnavailableError",
    "CacheBackend",
    "CacheKey",
    "CacheKeyBuilder",
    "CacheLock",
    "CacheManager",
    "CacheManagerProxy",
    "CacheXError",
    "CachedFunction",
    "InvalidTTLError",
    "LockTimeoutError",
    "ProxyNotSetError",
    "RequestNotFoundError",
    "TTLTypeError",
    "TTLValueError",
    "ValueTooLargeError",
    "VaryNormaliser",
    "__version__",
    "add_routes",
    "build_cache_key",
    "cache",
    "cached",
    "default_key_builder",
    "get_app_cache",
    "get_cache_backend",
    "invalidate",
    "invalidate_path",
]
