"""Proxy for CacheManager singleton access."""

from .manager import CacheManager
from .proxy import ProxyBase


class CacheManagerProxy(ProxyBase[CacheManager]):
    """FastAPI CacheX Proxy for CacheManager instance management."""

    _instance_type = CacheManager
    _required_methods = ("get", "set", "get_or_set")
