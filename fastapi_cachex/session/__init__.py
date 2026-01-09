"""Session management extension for FastAPI-CacheX."""

from .config import SessionConfig
from .dependencies import get_optional_session
from .dependencies import get_session
from .dependencies import get_session_manager
from .dependencies import require_session
from .manager import SessionManager
from .middleware import SessionMiddleware
from .models import Session
from .models import SessionUser
from .proxy import SessionManagerProxy

__all__ = [
    "Session",
    "SessionConfig",
    "SessionManager",
    "SessionManagerProxy",
    "SessionMiddleware",
    "SessionUser",
    "get_optional_session",
    "get_session",
    "get_session_manager",
    "require_session",
]
