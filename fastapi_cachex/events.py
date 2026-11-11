"""A hook for what the cache does, so an application can count it (#540).

The library logs at debug level and can send ``X-Cache``; neither gives an
application numbers. ``set_event_handler()`` registers one callable that is
told about every hit, miss, store and backend failure, and forwards them to
whatever it measures with: a Prometheus counter, an OpenTelemetry meter, a
log line. Nothing is built or called while no handler is set.
"""

import logging
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from ._callables import _is_coroutine_callable

logger = logging.getLogger(__name__)

EventKind = Literal[
    "hit", "miss", "stale", "refresh", "bypass", "store", "invalidate", "error"
]


@dataclass(frozen=True, slots=True)
class CacheEvent:
    """One thing the cache did.

    ``kind``:

    - ``hit``, ``miss``, ``stale``, ``refresh``, ``bypass``: how a request to
      a ``@cache`` route was answered (the values of ``X-Cache``), or for
      ``source="app"`` whether ``CacheManager.get()`` / ``get_or_set()``
      (and so ``@cached``) found the value. A request is reported when its
      handler raised too; a ``get_or_set()`` that waited for another caller
      to compute the value is a hit.
    - ``store``: a response or value was written.
    - ``invalidate``: an entry was deleted by ``invalidate()`` or
      ``CacheManager.delete()``.
    - ``error``: a backend call failed. For ``@cache``, any exception from
      the backend, whether the request then went on without the cache
      (``fail_open``) or failed with it. For ``CacheManager`` and
      ``@cached``, only a backend that could not be reached
      (``BackendUnavailableError``, raised as before): an error the server
      answered with, or another exception of a backend of your own, is not
      reported there.

    ``source`` is ``"http"`` for ``@cache`` and ``"app"`` for
    ``CacheManager`` and ``@cached``. ``key`` is the cache key, or None when
    the backend was not consulted or the call was not about one key; it holds client input (the query string,
    whatever a custom key builder puts in it), so it is not a label for a
    metric and not something to log as it is. ``route`` is the path template
    of the route (``/users/{user_id}``) for an event of a request to a
    ``@cache`` route. ``duration`` is the seconds it took to answer the
    request, on the events that answer one.
    """

    kind: EventKind
    source: Literal["http", "app"]
    key: str | None = None
    route: str | None = None
    duration: float | None = None


EventHandler = Callable[[CacheEvent], None]

_handler: EventHandler | None = None
# Set while the library reads through its own public API (``get_or_set``
# calling ``get``), so that one call is reported once.
_muted: ContextVar[bool] = ContextVar("fastapi_cachex_events_muted", default=False)


def set_event_handler(handler: EventHandler | None) -> None:
    """Register the callable told about cache events; ``None`` removes it.

    The handler is called synchronously, on the request's path: keep it to
    incrementing a counter or recording a number. It must be a plain
    function. An exception it raises is logged and does not reach the
    request.

    Raises:
        TypeError: If ``handler`` is not callable, or is an ``async``
            function: its coroutine would never be awaited.
    """
    if handler is not None and (
        not callable(handler) or _is_coroutine_callable(handler)
    ):
        msg = "the event handler must be a plain function taking the event"
        raise TypeError(msg)
    global _handler  # noqa: PLW0603 - one process-wide hook, like logging's
    _handler = handler


@contextmanager
def _mute() -> Iterator[None]:
    token = _muted.set(True)
    try:
        yield
    finally:
        _muted.reset(token)


def _emit(
    kind: EventKind,
    source: Literal["http", "app"],
    key: str | None = None,
    *,
    route: str | None = None,
    duration: float | None = None,
) -> None:
    """Tell the handler, if there is one."""
    handler = _handler
    if handler is None or _muted.get():
        return
    try:
        handler(CacheEvent(kind, source, key, route, duration))
    except Exception:
        logger.exception("The cache event handler raised; kind=%s", kind)


__all__ = ["CacheEvent", "EventHandler", "EventKind", "set_event_handler"]
