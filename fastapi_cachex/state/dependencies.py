"""FastAPI dependency injection utilities for state management."""

from typing import Annotated

from fastapi import Depends

from .manager import StateManager
from .proxy import StateManagerProxy


def get_state_manager() -> StateManager:
    """Dependency to get the application StateManager instance.

    Lazily creates and registers a default StateManager (backed by
    BackendProxy) the first time it's requested, unless one was already
    set via StateManagerProxy.set(...). Concurrent first calls share one
    instance.

    Unlike `AppCache`, it does not fall back to a `MemoryBackend`: OAuth
    states must be readable by whichever worker handles the callback, so with
    no backend configured it raises `BackendNotFoundError` and registers
    nothing.
    """
    return StateManagerProxy.get_or_create(StateManager)


StateManagerDep = Annotated[StateManager, Depends(get_state_manager)]
