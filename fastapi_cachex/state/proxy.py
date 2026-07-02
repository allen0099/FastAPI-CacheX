"""FastAPI CacheX Proxy for state manager management."""

from fastapi_cachex.proxy import ProxyBase

from .manager import StateManager


class StateManagerProxy(ProxyBase[StateManager]):
    """FastAPI CacheX Proxy for StateManager instance management."""
