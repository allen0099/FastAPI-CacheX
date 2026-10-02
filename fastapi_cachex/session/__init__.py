"""Session management extension for FastAPI-CacheX.

Deprecated in 0.4.0 and removed in 0.5.0 (#420): importing this package
emits a ``FutureWarning``.
"""

from fastapi_cachex._deprecation import SESSION_DEPRECATION as _SESSION_DEPRECATION
from fastapi_cachex._deprecation import warn_deprecated as _warn_deprecated

from .config import SessionConfig
from .dependencies import get_optional_session
from .dependencies import get_session
from .dependencies import get_session_client_ip
from .dependencies import get_session_manager
from .dependencies import login
from .dependencies import logout
from .dependencies import require_session
from .dependencies import require_user_session
from .dependencies import rotate_session_id
from .manager import SessionManager
from .middleware import FastAPICacheXSessionMiddleware
from .middleware import get_client_ip
from .models import Session
from .models import SessionUser
from .proxy import SessionManagerProxy

_warn_deprecated(_SESSION_DEPRECATION)

__all__ = [
    "FastAPICacheXSessionMiddleware",
    "Session",
    "SessionConfig",
    "SessionManager",
    "SessionManagerProxy",
    "SessionUser",
    "get_client_ip",
    "get_optional_session",
    "get_session",
    "get_session_client_ip",
    "get_session_manager",
    "login",
    "logout",
    "require_session",
    "require_user_session",
    "rotate_session_id",
]
