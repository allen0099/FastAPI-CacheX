"""Tests for StateManagerProxy and get_state_manager dependency."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import BackendNotFoundError
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.state import dependencies as state_dependencies
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


def test_get_state_manager_concurrent_first_calls_share_one_instance(
    memory_backend: MemoryBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastAPI runs the sync dependency in worker threads; racers must agree.

    Without a lock each first request built and registered its own
    `StateManager`, and the later `set()` replaced the earlier one.
    """

    class SlowStateManager(StateManager):
        def __init__(self) -> None:
            time.sleep(0.05)  # widen the window between the check and the set
            super().__init__()

    monkeypatch.setattr(state_dependencies, "StateManager", SlowStateManager)
    BackendProxy.set(memory_backend)
    StateManagerProxy.set(None)
    workers = 8
    barrier = threading.Barrier(workers)

    def first_call(_: int) -> StateManager:
        barrier.wait()
        return get_state_manager()

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            managers = list(pool.map(first_call, range(workers)))
        assert len({id(manager) for manager in managers}) == 1
        assert StateManagerProxy.get() is managers[0]
    finally:
        StateManagerProxy.set(None)


def test_get_state_manager_without_a_backend_raises_and_registers_nothing() -> None:
    """OAuth states need a shared backend, so there is no memory fallback."""
    BackendProxy.set(None)
    StateManagerProxy.set(None)

    with pytest.raises(BackendNotFoundError):
        get_state_manager()

    with pytest.raises(BackendNotFoundError):
        StateManagerProxy.get()
    with pytest.raises(BackendNotFoundError):
        BackendProxy.get()
