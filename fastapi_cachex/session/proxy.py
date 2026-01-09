"""FastAPI CacheX Proxy for session manager management."""

from fastapi_cachex.proxy import ProxyBase

from .manager import SessionManager


class SessionManagerProxy(ProxyBase[SessionManager]):
    """FastAPI CacheX Proxy for session manager management."""
