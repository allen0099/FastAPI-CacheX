"""FastAPI-CacheX: A powerful and flexible caching extension for FastAPI."""

from .cache import cache as cache
from .cache import default_key_builder as default_key_builder
from .dependencies import CacheBackend as CacheBackend
from .dependencies import get_cache_backend as get_cache_backend
from .proxy import BackendProxy as BackendProxy
from .routes import add_routes as add_routes
from .types import CacheKeyBuilder as CacheKeyBuilder

# Session management (optional feature)
__all__ = [
    "BackendProxy",
    "CacheBackend",
    "CacheKeyBuilder",
    "add_routes",
    "cache",
    "default_key_builder",
    "get_cache_backend",
]
