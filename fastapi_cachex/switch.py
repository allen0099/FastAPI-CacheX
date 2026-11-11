"""A process-wide switch that turns caching off (#557).

For local development, where a cached route hides the change just made, and
for tests that are not about caching. While it is off, ``@cache``,
``@cached`` and ``CacheManager.get_or_set()`` run the handler, function or
factory every time and neither read nor write the backend. A ``@cache``
route still sends its ``Cache-Control`` and ``ETag`` and still answers a
matching ``If-None-Match`` with 304, so clients see the same headers.

Explicit storage is not affected: ``CacheManager.get()`` / ``set()``, the
backends, ``CacheLock`` and the invalidation functions work as usual.
"""

import logging
import os

logger = logging.getLogger(__name__)

DISABLED_ENV = "FASTAPI_CACHEX_DISABLED"
_TRUE = frozenset({"1", "true", "yes", "on"})

# Read once, at import: the variable is for starting a process without the
# cache, not for switching while it runs (that is ``set_enabled``).
_enabled = os.environ.get(DISABLED_ENV, "").strip().lower() not in _TRUE
if not _enabled:
    # Loud: in production this would send every request to the handler.
    logger.warning(
        "Caching is off: %s is set. @cache, @cached and get_or_set() run "
        "the handler every time and store nothing.",
        DISABLED_ENV,
    )


def set_enabled(enabled: bool) -> None:
    """Turn caching on or off for this process (see the module docstring).

    Raises:
        TypeError: If ``enabled`` is not a ``bool``: a string such as
            ``"false"`` would otherwise turn caching on.
    """
    if not isinstance(enabled, bool):
        name = type(enabled).__name__  # type: ignore[unreachable]
        msg = f"enabled must be a bool, got {name}"
        raise TypeError(msg)
    global _enabled  # noqa: PLW0603 - one process-wide switch
    _enabled = enabled


def is_enabled() -> bool:
    """Whether caching is on: the default, unless turned off."""
    return _enabled


__all__ = ["DISABLED_ENV", "is_enabled", "set_enabled"]
