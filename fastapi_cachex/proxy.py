"""Backend proxy for managing cache backend instances."""

from __future__ import annotations

import threading
import warnings
from logging import getLogger
from typing import Generic
from typing import NoReturn
from typing import TypeVar

from .backends import BaseCacheBackend
from .backends import MemoryBackend
from .exceptions import BackendNotFoundError

ProxyInstance = TypeVar("ProxyInstance")

logger = getLogger(__name__)

# Serialises the lazy fallback below. `get_app_cache` is a sync dependency that
# FastAPI runs in a worker thread, so two first requests can reach it at once.
_fallback_lock = threading.Lock()


class ProxyMeta(type):
    """Metaclass for BackendProxy to prevent instantiation."""

    def __call__(cls) -> NoReturn:
        """Prevent instantiation of BackendProxy."""
        msg = "Proxy class cannot be instantiated. Use static methods instead."
        raise TypeError(msg)


class ProxyBase(Generic[ProxyInstance], metaclass=ProxyMeta):
    """Abstract base class for proxy classes."""

    _instance: ProxyInstance | None = None

    @classmethod
    def get(cls) -> ProxyInstance:
        """Get the current instance of the proxy.

        Returns:
            The current instance
        """
        if cls._instance is None:
            msg = f"No instance set for proxy {cls.__name__}"
            raise BackendNotFoundError(msg)
        return cls._instance

    @classmethod
    def set(cls, instance: ProxyInstance | None) -> None:
        """Set the instance for the proxy.

        Args:
            instance: The instance to set, or None to clear
        """
        logger.debug(
            "Setting instance to: <%s>",
            instance.__class__.__name__ if instance else "None",
        )
        cls._instance = instance


class BackendProxy(ProxyBase[BaseCacheBackend]):
    """FastAPI CacheX Proxy for backend management."""

    @staticmethod
    def get_backend() -> BaseCacheBackend:
        """Get the current backend instance.

        .. deprecated:: 0.3.0
            Use :meth:`get` instead. Will be removed in version 0.4.0.

        Returns:
            The current backend instance
        """
        warnings.warn(
            "get_backend() is deprecated, use get() instead. "
            "Will be removed in version 0.4.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return BackendProxy.get()

    @staticmethod
    def set_backend(backend: BaseCacheBackend | None) -> None:
        """Set the backend instance.

        .. deprecated:: 0.3.0
            Use :meth:`set` instead. Will be removed in version 0.4.0.

        Args:
            backend: The backend instance to set, or None to clear
        """
        warnings.warn(
            "set_backend() is deprecated, use set() instead. "
            "Will be removed in version 0.4.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        BackendProxy.set(backend)


def get_backend_or_fallback() -> BaseCacheBackend:
    """Return the configured backend, registering a `MemoryBackend` if none is.

    Used by `@cache` and the `AppCache` dependency. The check and the
    registration happen under one lock, so concurrent first callers — including
    ones on worker threads — all end up with the same fallback instead of each
    installing its own and overwriting the others.
    """
    try:
        return BackendProxy.get()
    except BackendNotFoundError:
        pass
    with _fallback_lock:
        try:
            return BackendProxy.get()
        except BackendNotFoundError:
            backend = MemoryBackend()
            BackendProxy.set(backend)
            logger.debug("No backend configured; using MemoryBackend fallback")
            return backend
