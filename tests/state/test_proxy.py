"""Tests for StateManagerProxy and get_state_manager dependency."""

import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.state.dependencies import get_state_manager
from fastapi_cachex.state.manager import StateManager
from fastapi_cachex.state.proxy import StateManagerProxy


def test_state_manager_proxy_get_set(memory_backend: MemoryBackend) -> None:
    """StateManagerProxy.set()/.get() round-trip a StateManager instance."""
    manager = StateManager(backend=memory_backend)
    StateManagerProxy.set(manager)
    try:
        assert StateManagerProxy.get() is manager
    finally:
        StateManagerProxy.set(None)


def test_state_manager_proxy_raises_when_unset() -> None:
    """StateManagerProxy.get() raises BackendNotFoundError when unset."""
    StateManagerProxy.set(None)
    with pytest.raises(BackendNotFoundError):
        StateManagerProxy.get()


def test_state_manager_proxy_cannot_be_instantiated() -> None:
    """StateManagerProxy cannot be instantiated due to ProxyMeta."""
    with pytest.raises(TypeError, match="Proxy class cannot be instantiated"):
        StateManagerProxy()


def test_get_state_manager_lazily_creates_default(
    memory_backend: MemoryBackend,
) -> None:
    """get_state_manager() lazily creates and registers a default StateManager."""
    BackendProxy.set(memory_backend)
    StateManagerProxy.set(None)
    try:
        manager = get_state_manager()
        assert isinstance(manager, StateManager)
        assert StateManagerProxy.get() is manager
    finally:
        StateManagerProxy.set(None)


def test_get_state_manager_reuses_existing_proxy_instance(
    memory_backend: MemoryBackend,
) -> None:
    """get_state_manager() reuses an already-set StateManagerProxy instance."""
    existing = StateManager(backend=memory_backend)
    StateManagerProxy.set(existing)
    try:
        assert get_state_manager() is existing
    finally:
        StateManagerProxy.set(None)
