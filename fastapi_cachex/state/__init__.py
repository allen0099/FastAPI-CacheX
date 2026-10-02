"""State management extension for FastAPI-CacheX.

Deprecated in 0.4.0 and removed in 0.5.0 (#420): importing this package
emits a ``FutureWarning``.
"""

from fastapi_cachex._deprecation import STATE_DEPRECATION as _STATE_DEPRECATION
from fastapi_cachex._deprecation import warn_deprecated as _warn_deprecated

from .dependencies import StateManagerDep as StateManagerDep
from .dependencies import get_state_manager as get_state_manager
from .exceptions import InvalidStateError as InvalidStateError
from .exceptions import StateDataError as StateDataError
from .exceptions import StateError as StateError
from .exceptions import StateExpiredError as StateExpiredError
from .manager import StateManager as StateManager
from .models import StateData as StateData
from .proxy import StateManagerProxy as StateManagerProxy

_warn_deprecated(_STATE_DEPRECATION)
