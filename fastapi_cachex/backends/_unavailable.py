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

P = ParamSpec("P")
R = TypeVar("R")

_SUBCLASSES: dict[type[Exception], type[BackendUnavailableError]] = {}


def _rebuild(error_type: type[Exception], args: tuple[Any, ...]) -> Exception:
    """Unpickle a translated error: pickle cannot import a runtime class."""
    return _subclass_for(error_type)(*args)


def _reduce(self: BaseException) -> tuple[Any, ...]:
    return _rebuild, (type(self).__bases__[1], self.args), self.__dict__ or None


def _subclass_for(error_type: type[Exception]) -> type[BackendUnavailableError]:
    """The ``BackendUnavailableError`` that is also an ``error_type``."""
    subclass = _SUBCLASSES.get(error_type)
    if subclass is None:
        # Named like its base, so a traceback reads BackendUnavailableError;
        # the chained original below it names the client's own exception.
        subclass = cast(
            "type[BackendUnavailableError]",
            type(
                "BackendUnavailableError",
                (BackendUnavailableError, error_type),
                {
                    "__module__": BackendUnavailableError.__module__,
                    "__qualname__": BackendUnavailableError.__qualname__,
                    "__doc__": BackendUnavailableError.__doc__,
                    "__reduce__": _reduce,
                },
            ),
        )
        _SUBCLASSES[error_type] = subclass
    return subclass


def as_unavailable(error: Exception) -> BackendUnavailableError:
    """``error`` as a ``BackendUnavailableError`` that is also its own type."""
    subclass = _subclass_for(type(error))
    try:
        translated = subclass(*error.args)
    except Exception:  # noqa: BLE001 - a constructor that wants other arguments
        translated = subclass(str(error))
    translated.__dict__.update(error.__dict__)
    return translated


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
