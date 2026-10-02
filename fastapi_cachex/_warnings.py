"""Point a warning at the application's line rather than the library's (#333)."""

import inspect


def caller_stacklevel() -> int:
    """Return the ``stacklevel`` of the first frame outside fastapi_cachex.

    For a ``warnings.warn`` in the function that calls this. The depth of a
    library call varies (a backend method called directly, through
    ``CacheManager`` or through a base-class fallback), so a fixed
    ``stacklevel`` names a library file on some paths.
    """
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:  # no frame support
        return 2
    # Level 1 is the function that warns; start at its caller.
    level, frame = 2, frame.f_back.f_back
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module != "fastapi_cachex" and not module.startswith("fastapi_cachex."):
            break
        frame = frame.f_back
        level += 1
    return level
