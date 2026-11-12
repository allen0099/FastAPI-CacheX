"""Core caching functionality and decorators."""

import logging
import time
import warnings
from collections.abc import Callable
from collections.abc import Iterable
from datetime import timedelta
from functools import wraps
from typing import Any
from typing import Literal

from fastapi import Request
from fastapi import Response

from ._arguments import _COOKIE_VARY_WARNING
from ._arguments import _BypassWarner
from ._arguments import _check_arguments
from ._arguments import _check_tags
from ._arguments import _no_store_ignored_warning
from ._arguments import _seconds
from ._cache_control import CacheControl
from ._cache_control import _build_cache_control
from ._invalidation import invalidate
from ._invalidation import invalidate_path
from ._invalidation import invalidate_tags
from ._key_builders import _resolve_key_builder
from ._key_builders import build_cache_key
from ._key_builders import default_key_builder
from ._params import _wrapper_params
from ._rendering import AsyncResponseCallable
from ._rendering import HandlerCallable
from ._rendering import _with_dependency_headers
from ._rendering import get_response
from ._route import _CACHED_METHODS
from ._route import _add_debug_headers
from ._route import _Call
from ._route import _ClientCacheControl
from ._route import _Route
from ._route import _route_template
from ._vary import Vary
from ._vary import _validate_vary
from .directives import DirectiveType
from .events import _emit
from .exceptions import RequestNotFoundError
from .headers import add_vary
from .proxy import get_backend_or_fallback
from .types import CacheKeyBuilder

# The public names of this module, kept when #422 split it into smaller ones.
__all__ = [
    "AsyncResponseCallable",
    "CacheControl",
    "HandlerCallable",
    "build_cache_key",
    "cache",
    "default_key_builder",
    "get_response",
    "invalidate",
    "invalidate_path",
    "invalidate_tags",
    "logger",
]

logger = logging.getLogger(__name__)


def cache(
    ttl: int | timedelta | None = None,
    stale_ttl: int | timedelta | None = None,
    *,
    stale: Literal["error", "revalidate"] | None = None,
    no_cache: bool = False,
    no_store: bool = False,
    public: bool = False,
    private: bool = False,
    immutable: bool = False,
    must_revalidate: bool = False,
    s_maxage: int | timedelta | None = None,
    proxy_revalidate: bool = False,
    no_transform: bool = False,
    key_builder: CacheKeyBuilder | None = None,
    fail_open: bool = True,
    cache_authorized: bool = False,
    vary: Vary | None = None,
    sort_query: bool | None = None,
    coalesce: bool | Literal["distributed"] = False,
    debug_header: bool = False,
    client_cache_control: _ClientCacheControl = False,
    serve_stale: bool = False,
    tags: Iterable[str] | Callable[[Request], Iterable[str]] | None = None,
) -> Callable[[HandlerCallable], AsyncResponseCallable]:
    """Cache decorator for FastAPI route handlers.

    Only GET and HEAD requests go through the cache; other methods run the
    handler unchanged. A HEAD request is answered like a GET: from the entry
    a GET stored under the same key (RFC 9110 §9.3.2), and with the same
    headers when the handler runs. Its own response is never stored, since a
    handler may render HEAD differently. The route must accept HEAD:
    ``@app.get`` does not, ``@app.api_route(..., methods=["GET", "HEAD"])``
    does.

    A response is never stored when the handler marks it ``private`` or
    ``no-store`` in its own ``Cache-Control`` (that header is then sent
    unchanged instead of the decorator's) or when it sets a cookie. Such a
    response is served as rendered, and an entry already stored under its key
    is left alone. A response that sets a cookie is sent with ``private`` in
    place of ``public`` (the other directives stay, except ``s-maxage`` and
    ``proxy-revalidate``), so that a shared cache in
    front of the app does not store it either.

    Args:
        ttl: How long a stored response may be served without running the
            handler, in seconds or as a ``timedelta`` of whole seconds. The
            same value is sent as ``max-age``.
            Without a positive ``ttl`` (``None``, or ``0``, which sends
            ``max-age=0``) nothing is read from or written to the backend: the
            handler runs on every request, and ``If-None-Match`` gets a 304
            only when it matches the freshly rendered response. Negative values
            are rejected.
        stale_ttl: Seconds (or a ``timedelta``) sent with the directive chosen
            by ``stale``. Without ``serve_stale`` it only shapes the
            ``Cache-Control`` header; the backend entry still expires after
            ``ttl``. Must be given together with ``stale``.
        stale: ``"revalidate"`` sends ``stale-while-revalidate=<stale_ttl>``,
            ``"error"`` sends ``stale-if-error=<stale_ttl>``.
        no_cache: Run the handler on every request and send ``no-cache``. The
            response is still stored when ``ttl`` is positive, and
            ``If-None-Match`` still gets a 304 when it matches the fresh ETag. The header then carries only
            ``no-cache`` (plus ``must-revalidate`` when set); ``ttl``,
            ``stale``, ``public``/``private`` and ``immutable`` are left out.
        no_store: Run the handler, store nothing, and send ``no-store``. Takes
            precedence over every other option.
        public: Send ``public``. Mutually exclusive with ``private``.
        private: Send ``private`` and bypass the shared backend entirely: the
            handler runs on every request and nothing is read or stored. ETag
            revalidation still works against the freshly rendered response.
            Mutually exclusive with ``public``.
        immutable: Send ``immutable``.
        must_revalidate: Send ``must-revalidate``.
        s_maxage: Send ``s-maxage=<seconds>``, the freshness lifetime for
            shared caches (a CDN or reverse proxy) in front of the app, in
            seconds or as a ``timedelta`` of whole seconds; ``0`` is sent as
            ``s-maxage=0``. It only shapes the header: the backend entry
            still expires after ``ttl``, and ``s_maxage`` works without a
            ``ttl``. Left out with ``no_cache``, and of the ``private``
            header a response gets when it sets a cookie or answers a
            request with credentials. Rejected with ``private=True``.
        proxy_revalidate: Send ``proxy-revalidate``: ``must-revalidate`` for
            shared caches only. Left out of the ``private`` header, like
            ``s_maxage``, and rejected with ``private=True``.
        no_transform: Send ``no-transform``, which tells intermediaries not
            to alter the body (recompress an image, say). Sent with
            ``no_cache`` and ``private`` too.
        key_builder: Custom function to build cache keys. If None, uses
            ``default_key_builder``. To add a component (user ID, tenant,
            locale) to the default key, return
            ``build_cache_key(request, component)``. Must be a sync callable
            returning a ``str``: an ``async def`` function, an object with an
            ``async def __call__`` or a ``functools.partial`` of either is
            rejected when the decorator is applied, and a builder that
            returns anything but a ``str`` fails the request with
            ``CacheXError`` (``fail_open`` does not apply; it covers backend
            errors only).
        fail_open: When the backend raises, log a warning and answer without
            the cache: a failed read counts as a miss and a failed write
            leaves the response unstored. ``False`` lets the error propagate,
            so the request fails.
        cache_authorized: Read and write the backend for requests that carry
            an ``Authorization`` header or a non-empty ``request.session``
            (from any session middleware, such as Starlette's
            ``SessionMiddleware``). By default such a request bypasses the
            backend as ``private=True`` does (RFC 9111 §3.5), unless
            ``public`` is set, and its response is sent with ``private``.
            With this option the response to such a request still carries
            ``private``: its entry is per caller only in this backend, while
            a shared cache downstream keys on the URL alone.
            A route without a positive ``ttl`` skips the backend anyway, but
            its response to such a request is still sent with ``private``;
            ``private=True`` routes send it already.
            On a route that reads the backend, the first such bypass is
            logged at ``WARNING`` once per route
            and credential kind (the route template and the kind, never the
            value), since it otherwise leaves the route with no cache hits.
            RFC 9111 would also allow reuse under ``must-revalidate``, but
            ``must_revalidate=True`` does not lift the bypass: only this
            explicit opt-in or ``public`` does. Set this only when
            ``key_builder`` puts the verified caller's identity into the key;
            with the default key builder one user's response would be served
            to the next.
        vary: Request header names the response depends on, e.g.
            ``["Accept-Language"]``. Each header's value (trimmed; empty when
            missing) is appended to the key, after whatever ``key_builder``
            returns, as a ``name=value`` component, so every distinct value
            gets its own entry. The names are also added to the ``Vary``
            header of every response to a GET or HEAD request, unless it already
            lists them or ``*``. Values are client-controlled: each listed
            header multiplies the number of entries. When only a few values
            matter, pass a mapping from the name to a normaliser instead:
            ``vary={"Accept-Language": pick_locale}`` calls
            ``pick_locale(value)`` and puts what it returns (a ``str`` or an
            ``int``) into the key, while ``Vary`` still names the header. A
            name mapped to ``None`` behaves as in a list. The normaliser
            must be a sync function; it runs once for every GET or HEAD
            request that is looked up in the cache, before the backend is
            read, and an exception it raises fails the request. The credential
            headers ``Authorization``, ``Proxy-Authorization``, ``Cookie``
            and ``X-Session-Token`` are keyed on ``sha256:<hex digest>`` of
            the value rather than the value, so no token or session cookie
            appears in the key; missing or empty, they stay ``name=``.
            A normalised value is hashed the same way. Listing ``Cookie``
            without a normaliser emits a ``UserWarning`` when the decorator
            is applied, since every visitor then gets their own entry.
            A request with ``Authorization`` or a non-empty
            ``request.session`` still bypasses the backend unless ``public``
            or ``cache_authorized`` is set, and a response that sets a cookie
            is still not stored.
        sort_query: Order the query parameters by name before building the
            key, so ``?a=1&b=2`` and ``?b=2&a=1`` share one entry. The sort is
            stable: repeated values of one name keep the order the client
            sent, so ``?tag=b&tag=a`` and ``?tag=a&tag=b`` stay distinct, and
            the names and values are encoded exactly as in the unsorted key.
            On by default (``None``); ``False`` keeps the order the client
            sent, for a handler whose response depends on it. Only the
            default key builder reads it: a custom ``key_builder`` is used as
            is (``build_cache_key()`` sorts unless told otherwise), and
            passing ``sort_query`` with one is rejected. Pass the same value
            to ``invalidate()``.
        coalesce: Run the handler once for concurrent misses of one key in
            this process. The first request to miss renders as usual; requests
            that miss the same key meanwhile wait for it to answer, then read
            the backend again and are served the stored entry. If the first
            response was not stored (it set a cookie, was ``private``, had an
            error status, was streamed, or the handler raised), each of them
            runs the handler itself, at once. A request whose backend read
            fails does not wait. Each worker process still runs
            the handler once per cold key, and a waiting request waits as
            long as the first one's handler takes. A HEAD request waits for a
            running GET but never makes others wait. Requires a positive
            ``ttl`` and is rejected with ``private`` or ``no_cache``, which
            never serve a stored entry.
            ``"distributed"`` does the same across the workers that share
            the backend: the request that leads in its process takes a
            ``CacheLock`` on the key while it renders, and a request in
            another worker waits until that lock is gone, then reads the
            backend again. An uncontended miss costs two more backend round
            trips (take and release); a hit costs none. The lock renews
            itself while the handler runs and expires within 30 seconds if
            its worker dies; a request waits for it 30 seconds at most and
            then runs the handler itself. With ``serve_stale`` one worker at a time
            refreshes a stale entry.
        debug_header: Send ``X-Cache`` on every GET or HEAD response, saying
            how it was answered: ``HIT`` for a response served from the
            stored entry (a 200, or a 304 for the entry's ETag), ``MISS``
            when the backend was consulted and the handler ran (a cold or
            expired key, ``no_cache``, or a response that was not stored),
            and ``BYPASS`` when the backend was neither read nor written
            (``no_store``, ``private``, no positive ``ttl``, or a request
            with credentials on a route without ``public`` or
            ``cache_authorized``). Off by default: it tells clients what the
            server caches. The header is never stored, and a stored
            ``X-Cache`` the handler set itself is replaced. A bypass, and
            a miss whose response was not stored, also carry
            ``X-Cache-Reason``, a ``CacheReason`` saying why. ``REFRESH``
            marks a request that ``client_cache_control`` let past the entry;
            as with ``MISS``, its response may not have been stored.
        serve_stale: Have this server's cache act on ``stale`` too, instead
            of only telling the caches downstream. The entry is then kept
            for ``ttl + stale_ttl``. Past ``ttl``, with ``stale="revalidate"``
            a request is answered from the old entry at once, and the handler
            runs after that response was sent, as a background task of the
            request, to store a new one (one such refresh per key and
            process at a time). With ``stale="error"`` the handler runs
            first, and if it raises or answers 5xx the old entry is sent
            instead. Such a response has ``X-Cache: STALE`` with
            ``debug_header`` and an ``Age`` above ``ttl``. Off by default.
        tags: Names this route's entries can be invalidated by, with
            ``invalidate_tags()``: a list of strings, or a plain function
            that takes the request and returns them
            (``lambda request: [f"user:{request.path_params['user_id']}"]``).
            The function runs on every request to the route, and must return
            the same tags for every request that shares a cache key (so:
            derive them from the path or query, not from a header the key
            does not hold), or the entry is stored again on each change.
            An entry is a miss once any of its tags has been invalidated
            since it was stored. A hit on a tagged entry costs one extra
            batched backend read; a route without tags pays nothing.
        client_cache_control: Let a request's own ``Cache-Control`` skip the
            stored entry. Off by default, so no client can send every
            request to the handler. With ``True``, or a callable that takes
            the request and returns whether this caller may, ``no-cache``
            or ``max-age=0`` runs the handler and stores the new response
            in place of the entry, and ``no-store`` runs the handler without
            reading or writing the backend. Other request directives stay
            ignored. The callable must be a plain function (an ``async``
            one is rejected), is only called for a request that carries one
            of these directives, and opens the route only by returning
            ``True`` itself.

    Returns:
        Decorator function that wraps route handlers with caching logic

    Raises:
        CacheXError: When the decorator is applied, if ``stale`` and
            ``stale_ttl`` are not given together, if ``public`` and
            ``private`` are both set, if ``s_maxage`` or
            ``proxy_revalidate`` is set with ``private``, if ``ttl``,
            ``stale_ttl`` or ``s_maxage`` is not an
            ``int`` or a ``timedelta`` of whole seconds, is negative or is
            larger than ``MAX_TTL``, or if ``vary`` is not a
            list of header field names (a single string is rejected), if
            ``sort_query`` is not a ``bool`` or is passed with
            ``key_builder``, if ``key_builder`` is an ``async`` callable, if
            ``coalesce`` is not a ``bool`` or ``"distributed"``, or is set
            without a positive
            ``ttl`` or with ``private`` or ``no_cache``, or if
            ``debug_header`` is not a ``bool``.
            At request time, if
            ``key_builder`` returns anything but a ``str``.

    Without ``ttl`` or any directive (a bare ``@cache()``), nothing is stored
    and the decorator has no ``Cache-Control`` of its own: it adds an ETag,
    answers a matching ``If-None-Match`` with 304 and keeps the handler's own
    ``Cache-Control``, except that a response setting a cookie or answering a
    request with credentials is still sent with ``private``.

    Warns:
        UserWarning: When the decorator is applied, if ``vary`` lists
            ``Cookie``, or if ``no_store`` is combined with another caching
            argument it overrides.
    """
    # Checked when `@cache(...)` is evaluated, which is at decoration time
    # too; the route built below sees the whole seconds.
    ttl = _seconds("ttl", ttl)
    stale_ttl = _seconds("stale_ttl", stale_ttl)
    s_maxage = _seconds("s_maxage", s_maxage)

    def decorator(func: HandlerCallable) -> AsyncResponseCallable:
        # Validate parameters eagerly at decoration time
        _check_arguments(
            ttl=ttl,
            stale=stale,
            stale_ttl=stale_ttl,
            no_cache=no_cache,
            no_store=no_store,
            public=public,
            private=private,
            coalesce=coalesce,
            debug_header=debug_header,
            client_cache_control=client_cache_control,
            serve_stale=serve_stale,
            must_revalidate=must_revalidate,
            s_maxage=s_maxage,
            proxy_revalidate=proxy_revalidate,
        )
        vary_names = _validate_vary(vary)
        builder = _resolve_key_builder(key_builder, sort_query)
        route_tags = _check_tags(tags)
        # A normaliser bounds the entries, which is what the warning is about.
        if any(
            name.lower() == "cookie" and normaliser is None
            for name, normaliser in vary_names.items()
        ):
            # stacklevel=2: the caller applying the decorator, i.e. the line
            # of the user's @cache(...).
            warnings.warn(_COOKIE_VARY_WARNING, UserWarning, stacklevel=2)
        # Arguments no_store overrides are accepted but warned about (#328);
        # rejecting them would break routes that work today.
        if no_store:
            ignored = [
                name
                for name, value in (
                    ("ttl", ttl),
                    ("stale", stale),
                    ("stale_ttl", stale_ttl),
                    ("no_cache", no_cache),
                    ("public", public),
                    ("private", private),
                    ("immutable", immutable),
                    ("must_revalidate", must_revalidate),
                    ("s_maxage", s_maxage),
                    ("proxy_revalidate", proxy_revalidate),
                    ("no_transform", no_transform),
                    ("coalesce", coalesce),
                    ("client_cache_control", client_cache_control),
                    ("serve_stale", serve_stale),
                    ("tags", tags),
                )
                if value is not None and value is not False
            ]
            if ignored:
                warnings.warn(
                    _no_store_ignored_warning(ignored), UserWarning, stacklevel=2
                )

        cache_control = _build_cache_control(
            ttl=ttl,
            stale=stale,
            stale_ttl=stale_ttl,
            no_cache=no_cache,
            public=public,
            private=private,
            immutable=immutable,
            must_revalidate=must_revalidate,
            s_maxage=s_maxage,
            proxy_revalidate=proxy_revalidate,
            no_transform=no_transform,
        )
        # The same header for one caller only: `private` instead of `public`,
        # and without `s-maxage` and `proxy-revalidate`, which speak to the
        # shared caches that must not store it.
        private_cache_control = _build_cache_control(
            ttl=ttl,
            stale=stale,
            stale_ttl=stale_ttl,
            no_cache=no_cache,
            public=False,
            private=True,
            immutable=immutable,
            must_revalidate=must_revalidate,
            no_transform=no_transform,
        )
        route = _Route(
            func=func,
            ttl=ttl,
            stale=stale,
            stale_ttl=stale_ttl,
            serve_stale=serve_stale,
            no_cache=no_cache,
            no_store=no_store,
            public=public,
            private=private,
            fail_open=fail_open,
            cache_authorized=cache_authorized,
            coalesce=coalesce,
            client_cache_control=client_cache_control,
            tags=route_tags,
            key_builder=builder,
            vary_names=vary_names,
            cache_control=cache_control,
            # `no_cache` leaves the scope out of the header, so it is added.
            private_cache_control=(
                f"{DirectiveType.PRIVATE.value}, {private_cache_control}"
                if no_cache
                else private_cache_control
            ),
            warn_bypass=_BypassWarner(
                f"{getattr(func, '__module__', '?')}."
                f"{getattr(func, '__qualname__', '?')}"
            ),
        )
        params = _wrapper_params(
            func, background=serve_stale and stale == "revalidate" and not no_store
        )

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Response:
            sub_response = params.take_response(kwargs)
            background = params.take_background(kwargs)
            request = params.take_request(kwargs)
            # Resolve backend on every request to support lifespan-configured backends
            backend = get_backend_or_fallback()
            if request is None:
                # Reached when the wrapper is called outside the router, which
                # is the only caller that supplies the request parameter.
                raise RequestNotFoundError
            # The dependencies have run; the handler has not (see
            # `_split_header_lines`).
            call = _Call(
                request=request,
                args=args,
                kwargs=kwargs,
                sub_response=sub_response,
                dependency_lines=(
                    [] if sub_response is None else list(sub_response.headers.raw)
                ),
                dependency_status=(
                    None if sub_response is None else sub_response.status_code
                ),
                background=background,
            )
            started = time.perf_counter()
            try:
                response = await route.respond(call, backend)
            finally:
                # Releases the requests waiting on this one (#252), then
                # the ones in other workers (#558).
                await call.finish()
                # Also when the handler raised: that request was a miss or a
                # bypass all the same.
                if call.outcome is not None:
                    _emit(
                        call.outcome.lower(),  # type: ignore[arg-type]
                        "http",
                        call.cache_key,
                        route=_route_template(request),
                        duration=time.perf_counter() - started,
                        reason=call.reason,
                    )
            cacheable_request = request.method in _CACHED_METHODS
            response = _with_dependency_headers(
                response,
                sub_response,
                call.dependency_lines,
                route.private_cache_control,
                cacheable_request=cacheable_request,
            )
            if debug_header:
                # Last, so that neither a dependency's line nor a stored one
                # stands in for this request's.
                _add_debug_headers(response, call)
            if vary_names and cacheable_request:
                # Every GET or HEAD answer, served from the backend or not: a
                # shared cache downstream keys on these headers too.
                add_vary(response.headers, list(vary_names))
            return response

        wrapper.__signature__ = params.signature  # type: ignore[attr-defined]
        return wrapper

    return decorator
