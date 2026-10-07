"""FastAPI-CacheX: A powerful and flexible caching extension for FastAPI."""

import logging
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    # Type checkers see the real types; at runtime `__getattr__` loads them.
    from .session import (
        FastAPICacheXSessionMiddleware as FastAPICacheXSessionMiddleware,
    )
    from .session import Session as Session
    from .session import SessionConfig as SessionConfig
    from .session import SessionManager as SessionManager
    from .session import SessionManagerProxy as SessionManagerProxy
    from .session import SessionUser as SessionUser
    from .session import get_optional_session as get_optional_session
    from .session import get_session as get_session
    from .session import get_session_manager as get_session_manager
    from .session import require_session as require_session
    from .session import require_user_session as require_user_session
    from .session.exceptions import SessionError as SessionError
    from .session.exceptions import SessionExpiredError as SessionExpiredError
    from .session.exceptions import SessionInvalidError as SessionInvalidError
    from .session.exceptions import SessionNotFoundError as SessionNotFoundError
    from .session.exceptions import SessionSecurityError as SessionSecurityError
    from .session.exceptions import SessionTokenError as SessionTokenError
    from .state import InvalidStateError as InvalidStateError
    from .state import StateData as StateData
    from .state import StateDataError as StateDataError
    from .state import StateError as StateError
    from .state import StateExpiredError as StateExpiredError
    from .state import StateManager as StateManager
    from .state import StateManagerDep as StateManagerDep
    from .state import StateManagerProxy as StateManagerProxy
    from .state import get_state_manager as get_state_manager


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

# Session and OAuth state names, deprecated in 0.4.0 and removed in 0.5.0
# (#420). They load on first use, so `import fastapi_cachex` does not import
# either package; importing one emits its FutureWarning. They are left out of
# `__all__`, so `from fastapi_cachex import *` does not load them either.
_DEPRECATED_NAMES = {
    "FastAPICacheXSessionMiddleware": "fastapi_cachex.session",
    "InvalidStateError": "fastapi_cachex.state",
    "Session": "fastapi_cachex.session",
    "SessionConfig": "fastapi_cachex.session",
    "SessionError": "fastapi_cachex.session.exceptions",
    "SessionExpiredError": "fastapi_cachex.session.exceptions",
    "SessionInvalidError": "fastapi_cachex.session.exceptions",
    "SessionManager": "fastapi_cachex.session",
    "SessionManagerProxy": "fastapi_cachex.session",
    "SessionNotFoundError": "fastapi_cachex.session.exceptions",
    "SessionSecurityError": "fastapi_cachex.session.exceptions",
    "SessionTokenError": "fastapi_cachex.session.exceptions",
    "SessionUser": "fastapi_cachex.session",
    "StateData": "fastapi_cachex.state",
    "StateDataError": "fastapi_cachex.state",
    "StateError": "fastapi_cachex.state",
    "StateExpiredError": "fastapi_cachex.state",
    "StateManager": "fastapi_cachex.state",
    "StateManagerDep": "fastapi_cachex.state",
    "StateManagerProxy": "fastapi_cachex.state",
    "get_optional_session": "fastapi_cachex.session",
    "get_session": "fastapi_cachex.session",
    "get_session_manager": "fastapi_cachex.session",
    "get_state_manager": "fastapi_cachex.state",
    "require_session": "fastapi_cachex.session",
    "require_user_session": "fastapi_cachex.session",
}


def __getattr__(name: str) -> object:
    """Resolve a deprecated session or state name on first use."""
    module_name = _DEPRECATED_NAMES.get(name)
    if module_name is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


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
