"""Backend proxy for managing cache backend instances."""

from __future__ import annotations

import warnings
from logging import getLogger
from typing import Generic
from typing import TypeVar

from .backends import BaseCacheBackend
from .exceptions import BackendNotFoundError

ProxyInstance = TypeVar("ProxyInstance")

logger = getLogger(__name__)


class ProxyMeta(type):
    """Metaclass for BackendProxy to prevent instantiation."""

    def __call__(cls) -> None:
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
        logger.info(
            "Setting instance to: <%s>",
            instance.__class__.__name__ if instance else "None",
        )
        cls._instance = instance


class BackendProxy(ProxyBase[BaseCacheBackend]):
    """FastAPI CacheX Proxy for backend management."""

    @staticmethod
    def get_backend() -> BaseCacheBackend:  # pragma: no cover
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
    def set_backend(backend: BaseCacheBackend | None) -> None:  # pragma: no cover
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
