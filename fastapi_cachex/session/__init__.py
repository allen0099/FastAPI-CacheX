"""Session management extension for FastAPI-CacheX."""

from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.dependencies import get_optional_session
from fastapi_cachex.session.dependencies import get_session
from fastapi_cachex.session.dependencies import require_session
from fastapi_cachex.session.manager import SessionManager
from fastapi_cachex.session.middleware import SessionMiddleware
from fastapi_cachex.session.models import Session
from fastapi_cachex.session.models import SessionUser

__all__ = [
    "Session",
    "SessionConfig",
    "SessionManager",
    "SessionMiddleware",
    "SessionUser",
    "get_optional_session",
    "get_session",
    "require_session",
]
