"""State management extension for FastAPI-CacheX."""

from .dependencies import StateManagerDep as StateManagerDep
from .dependencies import get_state_manager as get_state_manager
from .exceptions import InvalidStateError as InvalidStateError
from .exceptions import StateDataError as StateDataError
from .exceptions import StateError as StateError
from .exceptions import StateExpiredError as StateExpiredError
from .manager import StateManager as StateManager
from .models import StateData as StateData
from .proxy import StateManagerProxy as StateManagerProxy
