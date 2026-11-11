"""Turn a network backend's transport failures into ``BackendUnavailableError``.

The Redis and Memcached clients each raise their own exceptions when the
server cannot be reached: ``redis.exceptions.ConnectionError`` and
``TimeoutError``, socket errors, pymemcache's "All servers seem to be down".
A caller that only wants to know "the cache is down" would have to know which
backend is configured and import that client's exceptions (#260).

The backends' methods are wrapped so that such an error is raised again as a
``BackendUnavailableError`` chained to the original. The class raised is
built per original type and inherits from both, so it is also the original
type: an ``except redis.exceptions.ConnectionError`` or ``except OSError``
written before keeps catching it, and ``except BackendUnavailableError`` now
works on every backend.
"""

import functools
from collections.abc import Callable
from collections.abc import Coroutine
from typing import Any
from typing import ParamSpec
from typing import TypeVar
from typing import cast

from fastapi_cachex.exceptions import BackendUnavailableError
from fastapi_cachex.exceptions import CacheXError

P = ParamSpec("P")
R = TypeVar("R")

E = TypeVar("E", bound=CacheXError)

_SUBCLASSES: dict[tuple[type[CacheXError], type[Exception]], type[CacheXError]] = {}


def _rebuild(
    base: type[CacheXError], error_type: type[Exception], args: tuple[Any, ...]
) -> Exception:
    """Unpickle a translated error: pickle cannot import a runtime class."""
    return _subclass_for(base, error_type)(*args)


def _reduce(self: BaseException) -> tuple[Any, ...]:
    base, error_type = type(self).__bases__
    return _rebuild, (base, error_type, self.args), self.__dict__ or None


def _subclass_for(base: type[E], error_type: type[Exception]) -> type[E]:
    """The ``base`` (a ``CacheXError``) that is also an ``error_type``."""
    subclass = _SUBCLASSES.get((base, error_type))
    if subclass is None:
        # Named like its base, so a traceback reads BackendUnavailableError;
        # the chained original below it names the client's own exception.
        subclass = type(
            base.__name__,
            (base, error_type),
            {
                "__module__": base.__module__,
                "__qualname__": base.__qualname__,
                "__doc__": base.__doc__,
                "__reduce__": _reduce,
            },
        )
        _SUBCLASSES[base, error_type] = subclass
    return cast("type[E]", subclass)


def translated(base: type[E], error: Exception, message: str | None = None) -> E:
    """``error`` as a ``base`` that is also its own type.

    The arguments are the original's, or ``message`` alone when given.
    """
    subclass = _subclass_for(base, type(error))
    try:
        new = subclass(*(error.args if message is None else (message,)))
    except Exception:  # noqa: BLE001 - a constructor that wants other arguments
        new = subclass(str(error) if message is None else message)
    new.__dict__.update(error.__dict__)
    return new


def as_unavailable(error: Exception) -> BackendUnavailableError:
    """``error`` as a ``BackendUnavailableError`` that is also its own type."""
    return translated(BackendUnavailableError, error)


def translate_unavailable(
    is_unavailable: Callable[[Exception], bool],
) -> Callable[
    [Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]
]:
    """A decorator raising the errors ``is_unavailable`` accepts as unavailable."""

    def decorator(
        func: Callable[P, Coroutine[Any, Any, R]],
    ) -> Callable[P, Coroutine[Any, Any, R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await func(*args, **kwargs)
            except BackendUnavailableError:
                # Already translated by a wrapped method this one called.
                raise
            except Exception as e:
                if not is_unavailable(e):
                    raise
                raise as_unavailable(e) from e

        return wrapper

    return decorator
