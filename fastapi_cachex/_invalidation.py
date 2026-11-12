"""``invalidate``, ``invalidate_tags`` and ``invalidate_path`` (#562)."""

import logging
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

from fastapi import Request

from . import _tags
from ._key_builders import _append_key_components
from ._key_builders import _build_key
from ._key_builders import _resolve_key_builder
from ._vary import Vary
from ._vary import _validate_vary
from ._vary import _vary_components
from .events import _emit
from .exceptions import BackendNotFoundError
from .exceptions import CacheXError
from .proxy import BackendProxy
from .types import CacheKeyBuilder

# One logger for the decorator and the modules split from it.
logger = logging.getLogger("fastapi_cachex.cache")


async def invalidate(
    request: Request,
    key_builder: CacheKeyBuilder | None = None,
    vary: Vary | None = None,
    *,
    sort_query: bool | None = None,
) -> bool:
    """Invalidate the cache entry a ``@cache``-decorated route would use.

    Builds the same cache key the ``@cache`` decorator would build for
    ``request`` (via ``key_builder`` or ``default_key_builder``) and deletes
    it from the configured backend. Use this after a mutation to bust the
    cache for a specific cached route response.

    Args:
        request: The request whose cache key should be invalidated: a
            request with the method, path and query of the cached one. From
            another handler (a ``PUT`` that changes the resource), use
            ``invalidate_path()``, which builds that request.
        key_builder: Custom key builder used by the target route's ``@cache``
            decorator, if any. If None, uses ``default_key_builder``. Must be
            a sync callable returning a ``str``, as for ``@cache``.
        vary: The target route's ``vary``, if any: the same list of names
            or mapping of names to normalisers, so the same component is
            built. Only the variant selected by ``request``'s own values for
            those headers is deleted; ``clear_path()`` clears every variant of a path.
            Credential headers (``Authorization``, ``Cookie``, ...) are
            hashed exactly as ``@cache`` hashes them, so pass a request
            carrying the same header value.
        sort_query: The target route's ``sort_query``. By default the query
            is sorted as ``@cache`` sorts it, so ``?b=2&a=1`` deletes the
            entry stored for ``?a=1&b=2``. It is not read from the route: for
            a route with ``sort_query=False`` pass ``False`` here too, or the
            key will not match.

    Returns:
        True if a cache entry existed and was deleted, False otherwise.

    Raises:
        CacheXError: If ``vary`` is not a list of header names or a mapping
            of names to normalisers, if a normaliser returns something other
            than a ``str`` or an ``int``, if ``sort_query`` is not a ``bool``
            or is passed with ``key_builder``, if ``key_builder`` is an
            ``async`` callable, or if it returns something other than a
            ``str``. Raised before the
            backend is touched.
    """
    builder = _resolve_key_builder(key_builder, sort_query)
    vary_names = _validate_vary(vary)
    cache_key = _append_key_components(
        _build_key(builder, request), _vary_components(request, vary_names)
    )

    try:
        cache_backend = BackendProxy.get()
    except BackendNotFoundError:
        return False

    if await cache_backend.get_and_delete(cache_key) is None:
        if key_builder is None and request.method != "GET":
            # Only GET responses are stored, and the default key holds the
            # method, so this key can never exist: say so instead of
            # returning a False that reads like "nothing was cached".
            logger.warning(
                "invalidate() was given a %s request: its key cannot match a "
                "cached GET. Use invalidate_path(request, path) to delete the "
                "entry of a GET route from another handler.",
                request.method,
            )
        return False
    logger.debug("Cache INVALIDATE; key=%s", cache_key)
    _emit("invalidate", "http", cache_key)
    return True


async def invalidate_tags(*tags: str) -> None:
    """Invalidate every entry stored under any of ``tags``.

    Entries of ``@cache(tags=...)`` routes and values written with
    ``CacheManager.set(..., tags=...)``, ``get_or_set(..., tags=...)`` or
    ``@cached(tags=...)`` on the same backend share one set of tags. They
    read as a miss from now on, on every backend, Memcached included; the
    entries themselves stay in the backend until they expire.

    Without a backend there is nothing to invalidate, as for ``invalidate()``.

    Raises:
        TypeError: If a tag is not a ``str``.
        ValueError: If a tag is empty.
    """
    try:
        cache_backend = BackendProxy.get()
    except BackendNotFoundError:
        return
    await _tags.invalidate(cache_backend, tags)
    logger.debug("Cache INVALIDATE_TAGS; tags=%s", tags)


async def invalidate_path(
    request: Request,
    path: str,
    key_builder: CacheKeyBuilder | None = None,
    vary: Vary | None = None,
    *,
    query: str | Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
    sort_query: bool | None = None,
) -> bool:
    """Invalidate the entry a cached ``GET`` of ``path`` would use.

    For the handler that changes a resource (a ``PUT``, ``POST`` or
    ``DELETE``) and has to drop the cached ``GET`` of it. ``invalidate()``
    with that handler's own request deletes nothing, since it builds the key
    of the ``PUT``. This builds the request ``invalidate()`` needs: the
    current one with the method ``GET`` and the given path and query, so the
    host, the scheme and the headers a ``vary`` route keys on stay those of
    the caller::

        @app.put("/items/{item_id}")
        async def update_item(item_id: int, request: Request):
            await save(item_id)
            await invalidate_path(request, f"/items/{item_id}")

    Args:
        request: The current request. Its method, path and query are
            replaced; everything else in its scope (host, scheme, headers,
            and also ``path_params`` and ``state``, which a custom
            ``key_builder`` may read) stays the caller's.
        path: The path of the cached request as its handler sees it in
            ``request.url.path``: already percent-decoded, so build it from
            the decoded values (``f"/items/{item_id}"``), and with the prefix
            of a mounted app included. It may carry a query string
            (``"/items?page=2"``), so a literal ``?`` inside the path is
            written as ``request.url.path`` writes it (``%3F`` on current
            Starlette).
        key_builder: As for ``invalidate()``.
        vary: As for ``invalidate()``.
        query: The query of the cached request, when it is not part of
            ``path``: a string as it appears in the URL, percent-encoded
            (``"page=2"``), or a mapping or a sequence of pairs, which
            ``urllib.parse.urlencode`` encodes. Without one,
            the entry stored for the path without a query is deleted.
        sort_query: As for ``invalidate()``.

    Returns:
        True if a cache entry existed and was deleted, False otherwise.

    Raises:
        CacheXError: If ``path`` does not start with ``/``, if a query is
            given both in ``path`` and as ``query``, or as ``invalidate()``
            raises it.
    """
    path, _, embedded_query = path.partition("?")
    if not path.startswith("/"):
        msg = f"path must start with '/', got {path!r}"
        raise CacheXError(msg)
    if embedded_query and query is not None:
        msg = "pass the query either in path or as query, not both"
        raise CacheXError(msg)
    if query is None:
        query_string = embedded_query
    elif isinstance(query, str):
        query_string = query.removeprefix("?")
    else:
        query_string = urlencode(query, doseq=True)
    scope = {
        **request.scope,
        "method": "GET",
        # Taken as it is: the caller builds it from decoded values, and
        # decoding again would turn a literal "%25" into "%".
        "path": path,
        "raw_path": None,  # the caller's would name another path
        "query_string": query_string.encode("utf-8"),
    }
    return await invalidate(
        Request(scope, request.receive), key_builder, vary, sort_query=sort_query
    )
