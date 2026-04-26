"""FastAPI-CacheX: A powerful and flexible caching extension for FastAPI."""

import logging

from .cache import cache as cache
from .cache import default_key_builder as default_key_builder
from .dependencies import CacheBackend as CacheBackend
from .dependencies import get_cache_backend as get_cache_backend
from .proxy import BackendProxy as BackendProxy
from .routes import add_routes as add_routes
from .session import Session as Session
from .session import SessionConfig as SessionConfig
from .session import SessionManager as SessionManager
from .session import SessionManagerProxy as SessionManagerProxy
from .session import SessionMiddleware as SessionMiddleware
from .session import SessionUser as SessionUser
from .session import get_optional_session as get_optional_session
from .session import get_session as get_session
from .session import get_session_manager as get_session_manager
from .session import require_session as require_session
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
from .types import CacheKeyBuilder as CacheKeyBuilder

_package_logger = logging.getLogger("fastapi_cachex")
_package_logger.addHandler(
    logging.NullHandler()
)  # Attach a NullHandler to avoid "No handler found" warnings in user applications.

__all__ = [
    "BackendProxy",
    "CacheBackend",
    "CacheKeyBuilder",
    "InvalidStateError",
    "Session",
    "SessionConfig",
    "SessionError",
    "SessionExpiredError",
    "SessionInvalidError",
    "SessionManager",
    "SessionManagerProxy",
    "SessionMiddleware",
    "SessionNotFoundError",
    "SessionSecurityError",
    "SessionTokenError",
    "SessionUser",
    "StateData",
    "StateDataError",
    "StateError",
    "StateExpiredError",
    "StateManager",
    "add_routes",
    "cache",
    "default_key_builder",
    "get_cache_backend",
    "get_optional_session",
    "get_session",
    "get_session_manager",
    "require_session",
]
