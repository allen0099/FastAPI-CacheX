"""Telling whether a callable returns a coroutine, for handlers and key builders."""

import inspect
from collections.abc import Callable


def _is_coroutine_callable(func: Callable[..., object]) -> bool:
    """Report whether calling `func` returns a coroutine.

    `inspect.iscoroutinefunction` already sees through `functools.partial`;
    an instance with an ``async def __call__`` needs its method checked. The
    method is looked up on the type, as the call itself does, so a class
    (whose type is ``type``) counts as sync.
    """
    return inspect.iscoroutinefunction(func) or inspect.iscoroutinefunction(
        type(func).__call__
    )
