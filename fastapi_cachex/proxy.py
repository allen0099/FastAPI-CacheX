"""Backend proxy for managing cache backend instances."""

from __future__ import annotations

import threading
import warnings
from logging import getLogger
from typing import TYPE_CHECKING
from typing import ClassVar
from typing import Generic
from typing import NoReturn
from typing import TypeVar

from .backends import BaseCacheBackend
from .backends import MemoryBackend
from .exceptions import BackendNotFoundError
from .exceptions import ProxyNotSetError

if TYPE_CHECKING:
    from collections.abc import Callable

ProxyInstance = TypeVar("ProxyInstance")

logger = getLogger(__name__)


class ProxyMeta(type):
    """Metaclass for BackendProxy to prevent instantiation."""

    def __call__(cls) -> NoReturn:
        """Prevent instantiation of BackendProxy."""
        msg = "Proxy class cannot be instantiated. Use static methods instead."
        raise TypeError(msg)


class ProxyBase(Generic[ProxyInstance], metaclass=ProxyMeta):
    """Abstract base class for proxy classes."""

    _instance: ProxyInstance | None = None
    # Raised by `get()` while no instance is set.
    _not_set_error: ClassVar[type[BackendNotFoundError]] = ProxyNotSetError
    # Serialises `get_or_create`. A threading lock, because the sync FastAPI
    # dependencies built on it run in worker threads. One per class, so a
    # factory may call another proxy's `get_or_create` (the default
    # `CacheManager` needs a backend) without deadlocking.
    _create_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Give every proxy class its own creation lock."""
        super().__init_subclass__(**kwargs)
        cls._create_lock = threading.Lock()

    @classmethod
    def get(cls) -> ProxyInstance:
        """Get the current instance of the proxy.

        Returns:
            The current instance

        Raises:
            ProxyNotSetError: If no instance is set (``BackendNotFoundError``
                for ``BackendProxy``)
        """
        if cls._instance is None:
            msg = f"No instance set for proxy {cls.__name__}"
            raise cls._not_set_error(msg)
        return cls._instance

    @classmethod
    def get_or_create(cls, factory: Callable[[], ProxyInstance]) -> ProxyInstance:
        """Return the current instance, creating and registering one if unset.

        The check and the registration happen under the class's lock, so
        concurrent first callers, including ones on worker threads, all get
        the same instance: ``factory`` runs at most once. If it raises, nothing
        is registered and the error propagates.

        Args:
            factory: Builds the instance when none is set

        Returns:
            The registered instance
        """
        instance = cls._instance
        if instance is not None:
            return instance
        with cls._create_lock:
            instance = cls._instance
            if instance is None:
                instance = factory()
                cls.set(instance)
            return instance

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

    _not_set_error = BackendNotFoundError

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

    Used by `@cache`, `CacheBackend` and `AppCache`. Built on
    `BackendProxy.get_or_create`, so concurrent first callers, including ones
    on worker threads, all end up with the same fallback instead of each
    installing its own and overwriting the others.
    """
    return BackendProxy.get_or_create(_memory_fallback)


def _memory_fallback() -> BaseCacheBackend:
    logger.debug("No backend configured; using MemoryBackend fallback")
    return MemoryBackend()
