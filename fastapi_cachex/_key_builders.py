"""Cache keys for `@cache`: the default key builder and custom ones."""

import inspect
import logging
from collections.abc import Callable
from collections.abc import Sequence
from functools import partial

from fastapi import Request

from ._callables import _is_coroutine_callable
from .cache_key import CacheKey
from .exceptions import CacheXError
from .types import CACHE_KEY_SEPARATOR
from .types import CacheKeyBuilder
from .types import escape_key_component

# The documented logger: every part of `@cache` logs under this name.
logger = logging.getLogger("fastapi_cachex.cache")


def build_cache_key(
    request: Request, *components: str | int, sort_query: bool = True
) -> str:
    """Build the default cache key for ``request``, plus extra components.

    With no ``components`` the key is ``http:v2|method|host|path|query``,
    exactly what ``@cache`` uses by default. Each extra component is appended
    after another separator, so a custom ``key_builder`` can add a dimension
    (user ID, tenant, locale) without rebuilding the default key by hand::

        def per_user_key(request: Request) -> str:
            return build_cache_key(request, request.state.user_id)

    The host is the ``Host`` header lower-cased, without an empty or default
    port (``:80`` on http, ``:443`` on https), or ``unknown`` when there is
    none. ``|`` and ``%`` in the host, the path and every extra component are
    percent-encoded (see ``escape_key_component``), so none of them can
    contain the separator and make one request's key equal another's. The
    query string is already URL-encoded and never contains ``|``.

    Keys built this way keep the tag, method, host and path in front, so
    ``clear_path()`` still finds them and the monitoring routes still show
    their method, host, path and query. This is
    ``CacheKey.from_request(...).to_str()``; ``CacheKey.parse()`` decodes the
    key again.

    Args:
        request: The FastAPI Request object
        *components: Extra key components, appended in order. A ``str`` is
            used as is and an ``int`` is written in decimal, so ``1`` and
            ``"1"`` give the same key. An empty string is a component of its
            own: ``build_cache_key(request, "")`` differs from
            ``build_cache_key(request)``.
        sort_query: Order the query parameters by name (a stable sort, so
            ``?tag=b&tag=a`` stays distinct from ``?tag=a&tag=b``), so that
            ``?a=1&b=2`` and ``?b=2&a=1`` give the same key. On by default,
            as in ``@cache``; ``False`` keeps the order the client sent, as
            ``@cache(sort_query=False)`` does.

    Returns:
        Generated cache key string

    Raises:
        TypeError: If a component is not a ``str`` or ``int`` (``bool`` is
            rejected too), e.g. ``None`` from a missing user ID, which would
            otherwise put every such caller under one ``"None"`` key.
    """
    key = CacheKey.from_request(request, *components, sort_query=sort_query).to_str()
    logger.debug("Built cache key: %s", key)
    return key


def _append_key_components(key: str, components: Sequence[str]) -> str:
    """Append each component to ``key``, escaped, after another separator."""
    return CACHE_KEY_SEPARATOR.join(
        [key, *(escape_key_component(component) for component in components)]
    )


def default_key_builder(request: Request) -> str:
    """Default cache key builder function: ``build_cache_key(request)``.

    Generates cache key in format: http:v2|method|host|path|query, with the
    query parameters sorted by name.

    Kept as the name ``@cache`` and ``invalidate()`` fall back to. To add
    components to the default key, call ``build_cache_key`` instead.

    Args:
        request: The FastAPI Request object

    Returns:
        Generated cache key string
    """
    return build_cache_key(request)


def _unsorted_query_key_builder(request: Request) -> str:
    """The key builder of ``@cache(sort_query=False)``."""
    return build_cache_key(request, sort_query=False)


_SORT_QUERY_WITH_KEY_BUILDER_MSG = (
    "sort_query only applies to the default key builder; a custom key_builder "
    "builds its own key, so pass sort_query to build_cache_key() in it instead "
    "(it sorts unless told otherwise)"
)


def _resolve_key_builder(
    key_builder: CacheKeyBuilder | None, sort_query: object
) -> CacheKeyBuilder:
    """Pick the key builder for ``key_builder`` and ``sort_query``.

    ``sort_query=None`` means it was not passed: the default key builder
    sorts, and a custom one is used as is.

    Raises:
        CacheXError: If ``sort_query`` is not a ``bool`` or ``None``, if it
            is passed with a custom ``key_builder`` (the flag would silently
            do nothing), or if ``key_builder`` is an ``async`` callable.
    """
    if sort_query is not None and not isinstance(sort_query, bool):
        msg = f"sort_query must be a bool, got {type(sort_query).__name__}"
        raise CacheXError(msg)
    if key_builder is not None:
        if sort_query is not None:
            raise CacheXError(_SORT_QUERY_WITH_KEY_BUILDER_MSG)
        _validate_key_builder(key_builder)
        return key_builder
    return _unsorted_query_key_builder if sort_query is False else default_key_builder


_ASYNC_KEY_BUILDER_MSG = (
    "key_builder must be a sync function returning str; async key builders "
    "are not supported (the key is built without awaiting)"
)


def _validate_key_builder(builder: Callable[..., object]) -> None:
    """Reject a key builder whose call returns a coroutine.

    ``functools.partial`` layers are unwrapped first, so a partial of an async
    callable object is caught as well. A builder this cannot see through
    (say a sync wrapper returning a coroutine) is caught by ``_build_key``.

    Raises:
        CacheXError: If calling ``builder`` would return a coroutine.
    """
    target = builder
    while isinstance(target, partial):
        target = target.func
    if _is_coroutine_callable(target):
        raise CacheXError(_ASYNC_KEY_BUILDER_MSG)


def _build_key(builder: CacheKeyBuilder, request: Request) -> str:
    """Call ``builder`` and check that it returned a ``str``.

    A returned coroutine is closed, so it does not also trigger a "never
    awaited" ``RuntimeWarning``. ``fail_open`` does not cover this: it is a
    programming error in the route, not a backend failure.

    Raises:
        CacheXError: If ``builder`` returns anything but a ``str``.
    """
    key: object = builder(request)
    if isinstance(key, str):
        return key
    if inspect.iscoroutine(key):
        key.close()
        raise CacheXError(_ASYNC_KEY_BUILDER_MSG)
    msg = f"key_builder must return a str, got {type(key).__name__}"
    raise CacheXError(msg)
