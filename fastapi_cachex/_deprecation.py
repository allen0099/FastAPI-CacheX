"""Deprecation of the session and OAuth state subsystems (#420).

Both packages warn once, when they are first imported, and are removed in
0.5.0 (#421).
"""

import inspect
import warnings
from types import FrameType

_MIGRATION_URL = (
    "https://fastapi-cachex.readthedocs.io/en/latest/"
    "MIGRATING_0_4/#session-state-deprecated"
)

SESSION_DEPRECATION = (
    "fastapi_cachex.session is deprecated and will be removed in "
    "fastapi-cachex 0.5.0. Use Starlette's SessionMiddleware for signed-cookie "
    f"sessions, or a dedicated session library for server-side sessions; see {_MIGRATION_URL}"
)

STATE_DEPRECATION = (
    "fastapi_cachex.state is deprecated and will be removed in "
    "fastapi-cachex 0.5.0. Use the state handling of your OAuth client library "
    f"(Authlib, for example); see {_MIGRATION_URL}"
)


def _is_import_machinery(frame: FrameType) -> bool:
    """Whether ``warnings.warn()`` skips ``frame`` when it counts stacklevel."""
    filename = frame.f_code.co_filename
    return "importlib" in filename and "_bootstrap" in filename


def _importer_stacklevel() -> int:
    """Return the ``stacklevel`` of the first frame outside this package.

    Frames of fastapi_cachex and of the import machinery are skipped, so the
    warning names the application's ``import`` line, or the line that read a
    deprecated name from the ``fastapi_cachex`` package.
    """
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:  # no frame support
        return 2
    # Level 1 is warn_deprecated(); start at its caller.
    level, frame = 2, frame.f_back.f_back
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if not module.startswith(("fastapi_cachex.", "importlib.")) and module not in {
            "fastapi_cachex",
            "importlib",
        }:
            break
        # warnings.warn() does not count the frozen import machinery's frames
        # towards stacklevel, so neither may this.
        if not _is_import_machinery(frame):
            level += 1
        frame = frame.f_back
    return level


def warn_deprecated(message: str) -> None:
    """Emit the ``FutureWarning`` for a deprecated subsystem.

    ``FutureWarning`` rather than ``DeprecationWarning``: it is shown by
    default, and the removal affects the application, not only its tests.
    """
    warnings.warn(message, FutureWarning, stacklevel=_importer_stacklevel())
