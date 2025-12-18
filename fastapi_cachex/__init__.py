"""FastAPI-CacheX: A powerful and flexible caching extension for FastAPI."""

from .cache import cache as cache
from .dependencies import CacheBackend as CacheBackend
from .dependencies import get_cache_backend as get_cache_backend
from .proxy import BackendProxy as BackendProxy
from .routes import add_routes as add_routes

# Session management (optional feature)
__all__ = [
    "BackendProxy",
    "CacheBackend",
    "add_routes",
    "cache",
    "get_cache_backend",
]
