"""FastAPI dependency injection utilities for state management."""

from typing import Annotated

from fastapi import Depends

from fastapi_cachex.exceptions import BackendNotFoundError

from .manager import StateManager
from .proxy import StateManagerProxy


def get_state_manager() -> StateManager:
    """Dependency to get the application StateManager instance.

    Lazily creates and registers a default StateManager (backed by
    BackendProxy) the first time it's requested, unless one was already
    set via StateManagerProxy.set(...).
    """
    try:
        return StateManagerProxy.get()
    except BackendNotFoundError:
        manager = StateManager()
        StateManagerProxy.set(manager)
        return manager


StateManagerDep = Annotated[StateManager, Depends(get_state_manager)]
