"""Core caching functionality and decorators."""

import inspect
import logging
import threading
import warnings
from collections.abc import Callable
from collections.abc import Sequence
from functools import update_wrapper
from functools import wraps
from inspect import Parameter
from inspect import Signature
from typing import Annotated
from typing import Any
from typing import Literal
from typing import get_args
from typing import get_origin
from typing import get_type_hints

from fastapi import Request
from fastapi import Response
from fastapi.params import Depends as DependsParam

from ._cache_control import _NO_STORE
from ._cache_control import CacheControl
from ._cache_control import _build_cache_control
from ._cache_control import _cache_control_for
from ._cache_control import _unshareable_reason
from ._cache_control import _with_cache_control
from ._key_builders import _append_key_components
from ._key_builders import _build_key
from ._key_builders import _resolve_key_builder
from ._key_builders import build_cache_key
from ._key_builders import default_key_builder
from ._rendering import AsyncResponseCallable
from ._rendering import HandlerCallable
from ._rendering import _dependency_unshareable_reason
from ._rendering import _render
from ._rendering import _respond
from ._rendering import _with_dependency_headers
from ._rendering import get_response
from ._stored_response import _UNCACHEABLE_HEADERS
from ._stored_response import _age_headers
from ._stored_response import _append_headers
from ._stored_response import _entry_for
from ._stored_response import _etag_matches
from ._stored_response import _fresh_not_modified
from ._stored_response import _is_cacheable_status
from ._stored_response import _not_modified
from ._vary import _validate_vary
from ._vary import _vary_components
from .backends.base import MAX_TTL
from .directives import DirectiveType
from .exceptions import BackendNotFoundError
from .exceptions import CacheXError
from .exceptions import RequestNotFoundError
from .headers import add_vary
from .proxy import BackendProxy
from .proxy import get_backend_or_fallback
from .types import CacheKeyBuilder
from .types import log_ref

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
    "logger",
]

logger = logging.getLogger(__name__)

# Methods `@cache` answers; HEAD shares the GET entry (#253).
_CACHED_METHODS = frozenset({"GET", "HEAD"})


def _as_get(request: Request) -> Request:
    """The same request with method GET, to build the key a GET would get.

    ``state`` and the session live in the scope, so a key builder that reads
    them sees what the HEAD request carries.
    """
    return Request({**request.scope, "method": "GET"}, request.receive)


def _log_backend_failure(
    what: str, request: Request, cache_key: str, error: Exception
) -> None:
    """Log a failed backend call without writing the cache key at WARNING.

    The key holds the raw query string, ``vary`` header values and any custom
    key components, so the warning carries the method, the path and a digest
    of the key; the full key goes to ``DEBUG`` under the same digest. The path
    is formatted with ``%r`` because it is percent-decoded client input and
    could otherwise put a CR/LF into the log.
    """
    key_ref = log_ref(cache_key)
    logger.warning(
        "Cache backend %s. method=%s path=%r key_ref=%s error=%r",
        what,
        request.method,
        request.url.path,
        key_ref,
        error,
    )
    logger.debug("Cache backend %s; key_ref=%s key=%s", what, key_ref, cache_key)


# Where `FastAPICacheXSessionMiddleware` puts the session it loaded;
# `get_session` reads it.
_SESSION_STATE_KEY = "__fastapi_cachex_session"


def _request_credential(request: Request) -> str | None:
    """What identifies the caller of ``request``, or ``None`` if nothing does.

    ``Authorization``, a session the session middleware loaded (from the
    token header, a bearer token or the session cookie, anonymous or not), or
    a non-empty ``request.session`` from any session middleware (Starlette's
    cookie sessions included). Only a token that resolved to a session
    counts, so an invalid or expired one does not keep a request away from
    the cache.
    """
    if "authorization" in request.headers:
        return "Authorization header"
    if getattr(request.state, _SESSION_STATE_KEY, None) is not None:
        return "Session"
    if request.scope.get("session"):
        return "Session data"
    return None


_COOKIE_VARY_WARNING = (
    "cache vary on Cookie: @cache(vary=[...]) lists Cookie, so every distinct "
    "Cookie header gets its own entry and the number of entries grows with "
    "the number of visitors (and a new entry is made whenever any cookie "
    "changes). Key on the one value that matters instead, with a key_builder "
    "returning build_cache_key(request, <that cookie or the user id>), or use "
    "private=True. To keep vary=['Cookie'], silence this with "
    "warnings.filterwarnings('ignore', message='cache vary on Cookie')."
)


def _no_store_ignored_warning(ignored: list[str]) -> str:
    return (
        f"cache no_store ignores {', '.join(ignored)}: @cache(no_store=True) "
        "stores nothing and sends only Cache-Control: no-store, so the other "
        "arguments have no effect. Remove them, or drop no_store."
    )


# How the one-time bypass warning names each `_request_credential` result.
_CREDENTIAL_DESCRIPTIONS = {
    "Authorization header": "an Authorization header",
    "Session": "a session token (header, bearer token or cookie)",
    "Session data": "non-empty session data (request.session)",
}

_BYPASS_WARNING = (
    "@cache bypassed the shared backend for route %r: the request carried %s, "
    "so the response is not cached and is sent with Cache-Control: private. "
    "If the response is the same for every user, set @cache(public=True) "
    "(this also sends Cache-Control: public, so shared caches downstream may "
    "store it). If it is per user, set cache_authorized=True with a key_builder "
    "that puts the verified caller's identity into the key. Logged once per "
    "route and credential; each bypass is logged at DEBUG."
)


class _BypassWarner:
    """Log the credential bypass at ``WARNING`` once per route and credential.

    One instance lives in each ``@cache``-decorated function, so the state
    goes away with the function (and a test's fresh app starts with none).
    The route is keyed by its template (``/items/{item_id}``), never by the
    requested path, so the set stays bounded by the routes the function is
    registered on and client-chosen paths cannot grow it.
    """

    def __init__(self, fallback_name: str) -> None:
        self._fallback_name = fallback_name
        self._warned: set[tuple[str, str]] = set()
        # The wrapper runs on an event loop, where check-then-add cannot be
        # interleaved, but one decorated function can serve apps running on
        # several loops in different threads (several servers in one process,
        # each `TestClient`), and a set's check-then-add is not atomic across
        # threads. The lock keeps "once" exact; it costs nothing on the hot
        # path, which returns before taking it once the pair is recorded.
        self._lock = threading.Lock()

    def __call__(self, request: Request, credential: str) -> None:
        route_path = getattr(request.scope.get("route"), "path", None)
        # Without a matched route (not reachable through FastAPI's router),
        # name the handler: the requested path is client input and unbounded.
        route = route_path if isinstance(route_path, str) else self._fallback_name
        pair = (route, credential)
        if pair in self._warned:
            return
        with self._lock:
            if pair in self._warned:
                return
            self._warned.add(pair)
        # Only the route template and the credential kind: never a header
        # value or token. `%r` escapes anything unusual in the template.
        logger.warning(
            _BYPASS_WARNING,
            route,
            _CREDENTIAL_DESCRIPTIONS.get(credential, credential),
        )


async def invalidate(
    request: Request,
    key_builder: CacheKeyBuilder | None = None,
    vary: Sequence[str] | None = None,
    *,
    sort_query: bool | None = None,
) -> bool:
    """Invalidate the cache entry a ``@cache``-decorated route would use.

    Builds the same cache key the ``@cache`` decorator would build for
    ``request`` (via ``key_builder`` or ``default_key_builder``) and deletes
    it from the configured backend. Use this after a mutation to bust the
    cache for a specific cached route response.

    Args:
        request: The request whose cache key should be invalidated. Typically
            a request to the same route/method as the cached one (e.g. build
            it via ``request.app.url_path_for(...)`` for a GET route).
        key_builder: Custom key builder used by the target route's ``@cache``
            decorator, if any. If None, uses ``default_key_builder``. Must be
            a sync callable returning a ``str``, as for ``@cache``.
        vary: The target route's ``vary`` names, if any. Only the variant
            selected by ``request``'s own values for those headers is
            deleted; ``clear_path()`` clears every variant of a path.
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
        CacheXError: If ``vary`` is not a list of header names, if
            ``sort_query`` is not a ``bool`` or is passed with
            ``key_builder``, if ``key_builder`` is an ``async`` callable, or
            if it returns something other than a ``str``. Raised before the
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
        return False
    logger.debug("Cache INVALIDATE; key=%s", cache_key)
    return True


def _is_request_annotation(annotation: Any) -> bool:
    """Whether an annotation asks for a ``Request`` (or a subclass of one)."""
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return isinstance(annotation, type) and issubclass(annotation, Request)


def _is_response_annotation(annotation: Any) -> bool:
    """Whether an annotation asks for the sub-``Response`` FastAPI injects.

    ``Annotated[Response, Depends(...)]`` is a dependency that returns a
    ``Response``, not the sub-response.
    """
    if get_origin(annotation) is Annotated:
        annotation, *metadata = get_args(annotation)
        if any(isinstance(item, DependsParam) for item in metadata):
            return False
    return isinstance(annotation, type) and issubclass(annotation, Response)


def _find_request_param(
    func: HandlerCallable, params: list[Parameter]
) -> Parameter | None:
    """The handler's own ``Request`` parameter, if it declares one (see ``_find_param``)."""
    return _find_param(func, params, _is_request_annotation)


def _find_response_param(
    func: HandlerCallable, params: list[Parameter]
) -> Parameter | None:
    """The handler's own ``Response`` parameter, if it declares one (see ``_find_param``).

    A parameter defaulting to ``Depends(...)`` is a dependency's result, not
    the sub-response, whatever its annotation.
    """
    return _find_param(
        func,
        [param for param in params if not isinstance(param.default, DependsParam)],
        _is_response_annotation,
    )


def _find_param(
    func: HandlerCallable,
    params: list[Parameter],
    matches: Callable[[Any], bool],
) -> Parameter | None:
    """The handler's first parameter whose annotation ``matches``.

    Annotations are resolved first, so a handler under ``from __future__ import
    annotations`` (where the annotation is the string ``"Request"``),
    ``Annotated[Request, ...]``, or a ``Request`` subclass is recognised
    instead of being given a second, unused parameter. Resolution can
    fail on a forward reference that does not resolve in the handler's module,
    which must not break decoration: the raw annotations are used instead.
    """
    try:
        hints = get_type_hints(inspect.unwrap(func), include_extras=True)
    except Exception:  # noqa: BLE001 - any resolution failure falls back
        hints = {}

    return next(
        (param for param in params if matches(hints.get(param.name, param.annotation))),
        None,
    )


def cache(
    ttl: int | None = None,
    stale_ttl: int | None = None,
    *,
    stale: Literal["error", "revalidate"] | None = None,
    no_cache: bool = False,
    no_store: bool = False,
    public: bool = False,
    private: bool = False,
    immutable: bool = False,
    must_revalidate: bool = False,
    key_builder: CacheKeyBuilder | None = None,
    fail_open: bool = True,
    cache_authorized: bool = False,
    vary: Sequence[str] | None = None,
    sort_query: bool | None = None,
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
    place of ``public`` (the other directives stay), so that a shared cache in
    front of the app does not store it either.

    Args:
        ttl: How long, in seconds, a stored response may be served without
            running the handler. The same value is sent as ``max-age``.
            Without a positive ``ttl`` (``None``, or ``0``, which sends
            ``max-age=0``) nothing is read from or written to the backend: the
            handler runs on every request, and ``If-None-Match`` gets a 304
            only when it matches the freshly rendered response. Negative values
            are rejected.
        stale_ttl: Seconds sent with the directive chosen by ``stale``. It only
            shapes the ``Cache-Control`` header; the backend entry still
            expires after ``ttl``. Must be given together with ``stale``.
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
            an ``Authorization`` header or arrive with a session: one the
            session middleware loaded (from its token header, a bearer token
            or the session cookie, with or without a user) or a non-empty
            ``request.session``. By default such a request bypasses the
            backend as ``private=True`` does (RFC 9111 §3.5), unless
            ``public`` is set, and its response is sent with ``private``.
            With this option the response to such a request still carries
            ``private``: its entry is per caller only in this backend, while
            a shared cache downstream keys on the URL alone.
            A token that does not resolve to a session does not count.
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
            header multiplies the number of entries, so normalise them in a
            ``key_builder`` when only a few values matter. The credential
            headers ``Authorization``, ``Proxy-Authorization``, ``Cookie``
            and ``X-Session-Token`` are keyed on ``sha256:<hex digest>`` of
            the value rather than the value, so no token or session cookie
            appears in the key; missing or empty, they stay ``name=``.
            Listing ``Cookie`` emits a ``UserWarning`` when the decorator is
            applied, since every visitor then gets their own entry.
            A request with ``Authorization`` or a session still bypasses the
            backend unless ``public`` or ``cache_authorized`` is set, and a response
            that sets a cookie is still not stored.
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

    Returns:
        Decorator function that wraps route handlers with caching logic

    Raises:
        CacheXError: When the decorator is applied, if ``stale`` and
            ``stale_ttl`` are not given together, if ``public`` and
            ``private`` are both set, if ``ttl`` is not an ``int``, is
            negative or is larger than ``MAX_TTL``, or if ``vary`` is not a
            list of header field names (a single string is rejected), if
            ``sort_query`` is not a ``bool`` or is passed with
            ``key_builder``, or if ``key_builder`` is an ``async`` callable.
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

    def decorator(func: HandlerCallable) -> AsyncResponseCallable:
        # Validate parameters eagerly at decoration time
        if stale is not None and stale_ttl is None:
            msg = "stale_ttl must be set if stale is used"
            raise CacheXError(msg)
        if stale_ttl is not None and stale is None:
            msg = "stale must be set if stale_ttl is used"
            raise CacheXError(msg)
        if public and private:
            msg = "public and private are mutually exclusive"
            raise CacheXError(msg)
        if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int)):
            # Checked here: at request time the backend would reject it, and
            # failing open would hide that the route never caches.
            msg = f"ttl must be an int number of seconds, got {type(ttl).__name__}"
            raise CacheXError(msg)
        if ttl is not None and ttl < 0:
            msg = "ttl must not be negative"
            raise CacheXError(msg)
        if ttl is not None and ttl > MAX_TTL:
            msg = f"ttl must be at most {MAX_TTL} seconds"
            raise CacheXError(msg)
        vary_names = _validate_vary(vary)
        builder = _resolve_key_builder(key_builder, sort_query)
        if any(name.lower() == "cookie" for name in vary_names):
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
                )
                if value is not None and value is not False
            ]
            if ignored:
                warnings.warn(
                    _no_store_ignored_warning(ignored), UserWarning, stacklevel=2
                )

        # Analyze the original function's signature
        sig: Signature = inspect.signature(func)
        params: list[Parameter] = list(sig.parameters.values())

        # Check if Request is already in the parameters
        found_request: Parameter | None = _find_request_param(func, params)

        # FastAPI's sub-response: the wrapper needs it even when the handler
        # does not ask for it, to send what the dependencies set on it (#233).
        found_response: Parameter | None = _find_response_param(func, params)

        # Add the Request and Response parameters the handler does not declare
        injected: list[Parameter] = []
        if not found_request:
            request_name: str = "__cachex_request"
            injected.append(
                inspect.Parameter(
                    request_name, inspect.Parameter.KEYWORD_ONLY, annotation=Request
                )
            )
        else:
            request_name = found_request.name
        if not found_response:
            response_name: str = "__cachex_response"
            injected.append(
                inspect.Parameter(
                    response_name, inspect.Parameter.KEYWORD_ONLY, annotation=Response
                )
            )
        else:
            response_name = found_response.name

        if injected:
            # A keyword-only parameter must precede **kwargs; appending it
            # after one makes `Signature.replace` raise at decoration time,
            # so a handler taking **kwargs could not be cached at all.
            insert_at = next(
                (
                    index
                    for index, param in enumerate(params)
                    if param.kind is Parameter.VAR_KEYWORD
                ),
                len(params),
            )
            sig = sig.replace(
                parameters=[*params[:insert_at], *injected, *params[insert_at:]]
            )

        # The header only depends on the decorator arguments, so build it once.
        cache_control = _build_cache_control(
            ttl=ttl,
            stale=stale,
            stale_ttl=stale_ttl,
            no_cache=no_cache,
            public=public,
            private=private,
            immutable=immutable,
            must_revalidate=must_revalidate,
        )
        # Sent instead for a response that must not be stored downstream
        # either: one that sets a cookie, or one answering an `Authorization`
        # or session request the backend was bypassed for. `public` becomes `private`;
        # `no_cache` leaves the scope out of `cache_control`, so it is added.
        private_cache_control = (
            f"{DirectiveType.PRIVATE.value}, {cache_control}"
            if no_cache
            else _build_cache_control(
                ttl=ttl,
                stale=stale,
                stale_ttl=stale_ttl,
                no_cache=no_cache,
                public=False,
                private=True,
                immutable=immutable,
                must_revalidate=must_revalidate,
            )
        )
        # Without a positive ttl nothing may be served from storage, and a 304
        # answered from a stored ETag would be exactly that: it would keep
        # confirming a copy that the handler no longer produces (#110). Such
        # routes skip the backend like private ones. `ttl=0` is included, since
        # `max-age=0` allows no reuse either.
        bypass_backend = private or not ttl
        warn_bypass = _BypassWarner(
            f"{getattr(func, '__module__', '?')}.{getattr(func, '__qualname__', '?')}"
        )

        async def respond(
            sub_response: Response | None,
            dependency_lines: Sequence[tuple[bytes, bytes]],
            dependency_status: int | None,
            /,
            *args: Any,
            **kwargs: Any,
        ) -> Response:
            # Resolve backend on every request to support lifespan-configured backends
            cache_backend = get_backend_or_fallback()

            if found_request:
                req: Request | None = kwargs.get(request_name)
            else:
                req = kwargs.pop(request_name, None)

            if req is None:
                # Reached when the wrapper is called outside the router, which
                # is the only caller that supplies the request parameter.
                raise RequestNotFoundError

            if req.method not in _CACHED_METHODS:
                logger.debug(
                    "Not GET or HEAD; bypassing cache for method=%s", req.method
                )
                return await _respond(
                    func,
                    req,
                    args,
                    kwargs,
                    sub_response=sub_response,
                    dependency_lines=dependency_lines,
                )

            # Handle special case: no-store (highest priority)
            if no_store:
                response = await _respond(
                    func,
                    req,
                    args,
                    kwargs,
                    sub_response=sub_response,
                    dependency_lines=dependency_lines,
                )
                logger.debug(
                    "no-store active; bypassed cache for path=%s", req.url.path
                )
                # Unconditional: `no-store` is stricter than anything the
                # handler may have sent.
                response.headers["Cache-Control"] = _NO_STORE
                return response

            client_etag = req.headers.get("if-none-match")

            # RFC 9111 §3.5: a shared cache must not reuse a response to a
            # request with `Authorization` unless the response allows it. The
            # default key carries no identity, so treat such requests as
            # private unless the route is `public` or opted in. A request that
            # arrived with a session is the same case, whichever transport
            # carried its token (#319). Routes without a positive ttl skip the
            # backend anyway, but their response still needs `private` for a
            # downstream cache (#362); only `private=True` already sends it.
            # `cache_authorized` lifts the bypass but not `private`: its entries
            # are per caller only in this backend, while a shared cache
            # downstream keys on the URL alone (#372).
            credential = None if private or public else _request_credential(req)
            authorized_bypass = credential is not None and not cache_authorized
            response_cache_control = (
                cache_control if credential is None else private_cache_control
            )
            if credential is not None and not cache_authorized:
                logger.debug(
                    "%s present; bypassing the backend for path=%s",
                    credential,
                    req.url.path,
                )
                # The warning is about lost cache hits, which a route that
                # never reads the backend does not have.
                if not bypass_backend:
                    warn_bypass(req, credential)

            # A private response belongs to exactly one user, so it must never
            # be read from or written to the shared backend — the default cache
            # key carries no identity, so a stored copy would be served to the
            # next caller. The same path serves routes without a positive ttl
            # (see `bypass_backend`). ETag revalidation still works: it
            # compares the client's validator against freshly rendered content.
            if bypass_backend or authorized_bypass:
                # `response_cache_control` has `private` for a request with
                # credentials: RFC 9111 §3.5 would still let a downstream shared
                # cache reuse the answer to an `Authorization` request under
                # `must-revalidate`, and nothing at all stops one reusing the
                # answer to a cookie.
                response, _, etag = await _render(
                    func,
                    req,
                    args,
                    kwargs,
                    sub_response=sub_response,
                    dependency_lines=dependency_lines,
                )
                if not _is_cacheable_status(response.status_code):
                    return response
                if etag is None:
                    # StreamingResponse/FileResponse — cannot compute ETag
                    return _with_cache_control(
                        response, response_cache_control, private_cache_control
                    )
                not_modified = _fresh_not_modified(
                    response,
                    etag,
                    client_etag,
                    _cache_control_for(
                        response, response_cache_control, private_cache_control
                    ),
                )
                if not_modified is not None:
                    logger.debug("304 Not Modified (uncached); path=%s", req.url.path)
                    return not_modified
                response.headers["ETag"] = etag
                logger.debug("Bypassed the backend; path=%s", req.url.path)
                return _with_cache_control(
                    response, response_cache_control, private_cache_control
                )

            # Built only here: the branches above never touch the backend, so a
            # custom key builder would run for nothing.
            # HEAD reads the entry of the GET (#253), whichever builder made
            # the key.
            cache_key = _append_key_components(
                _build_key(builder, _as_get(req) if req.method == "HEAD" else req),
                _vary_components(req, vary_names),
            )

            try:
                cached_data = await cache_backend.get(cache_key)
            except Exception as e:
                if not fail_open:
                    raise
                _log_backend_failure("read failed; serving uncached", req, cache_key, e)
                cached_data = None

            current_response: Response | None = None
            current_body: bytes | None = None
            current_etag: str | None = None

            if client_etag:
                if no_cache:
                    # Get fresh response first if using no-cache
                    current_response, current_body, current_etag = await _render(
                        func,
                        req,
                        args,
                        kwargs,
                        sub_response=sub_response,
                        dependency_lines=dependency_lines,
                    )
                    if not _is_cacheable_status(current_response.status_code):
                        # Error responses carry no validator: never answer 304.
                        logger.debug(
                            "Uncacheable status %s; serving as-is for key=%s",
                            current_response.status_code,
                            cache_key,
                        )
                        return current_response

                    if current_etag is None:
                        # StreamingResponse/FileResponse — cannot compute ETag; serve as-is
                        return _with_cache_control(
                            current_response,
                            response_cache_control,
                            private_cache_control,
                        )

                    # For no-cache, compare fresh data with client's ETag
                    not_modified = _fresh_not_modified(
                        current_response,
                        current_etag,
                        client_etag,
                        _cache_control_for(
                            current_response,
                            response_cache_control,
                            private_cache_control,
                        ),
                    )
                    if not_modified is not None:
                        logger.debug("304 Not Modified via no-cache; key=%s", cache_key)
                        return not_modified

                # Compare with cached ETag - if match, return 304
                elif cached_data and _etag_matches(
                    client_etag, cached_data.fingerprint
                ):
                    logger.debug(
                        "304 Not Modified (cached ETag match); key=%s", cache_key
                    )
                    # Answered from the stored entry, so the 304 says how old
                    # that entry is: a cache refreshing its copy with this 304
                    # takes the new Age with it (RFC 9111 §4.3.4).
                    return _not_modified(
                        cached_data.fingerprint,
                        response_cache_control,
                        cached_data.headers,
                        _age_headers(cached_data, ttl),
                    )

            # No 304 was sent (no If-None-Match, or it did not match): serve a
            # valid cached copy directly (cache hit without running the handler)
            if cached_data and not no_cache:
                logger.debug("Cache HIT (TTL valid); key=%s", cache_key)
                hit = Response(
                    content=cached_data.content,
                    status_code=cached_data.status_code,
                    media_type=cached_data.media_type,
                )
                # Framing and validator headers come from this response, not
                # the entry: an entry built outside @cache may still carry them.
                _append_headers(
                    hit,
                    (
                        (name, value)
                        for name, value in cached_data.headers
                        if name.lower() not in _UNCACHEABLE_HEADERS
                    ),
                )
                hit.headers["ETag"] = cached_data.fingerprint
                hit.headers["Cache-Control"] = response_cache_control
                hit.headers.update(_age_headers(cached_data, ttl))
                return hit

            if current_response is None or current_etag is None:
                # Retrieve the current response if not already done
                current_response, current_body, current_etag = await _render(
                    func,
                    req,
                    args,
                    kwargs,
                    sub_response=sub_response,
                    dependency_lines=dependency_lines,
                )
                if not _is_cacheable_status(current_response.status_code):
                    # Leave any existing entry alone: a transient error must not
                    # evict or overwrite the last good response.
                    logger.debug(
                        "Uncacheable status %s; not storing key=%s",
                        current_response.status_code,
                        cache_key,
                    )
                    return current_response

                if current_etag is None:
                    # StreamingResponse/FileResponse — cannot compute ETag; serve as-is
                    return _with_cache_control(
                        current_response, response_cache_control, private_cache_control
                    )
                logger.debug("Cache MISS; computed fresh ETag for key=%s", cache_key)

            current_response.headers["ETag"] = current_etag

            # A response the handler scoped to one caller is served but never
            # stored. An entry already under this key is left alone, as for an
            # error status: it came from a response that was shareable.
            skip_reason = _unshareable_reason(current_response)
            if skip_reason is None:
                skip_reason = _dependency_unshareable_reason(dependency_lines)
            if skip_reason is None and dependency_status is not None:
                # It may hold for this request only, and a hit would replay it.
                skip_reason = "a dependency set the status code"
            if skip_reason is None and req.method == "HEAD":
                # A handler may skip the body for HEAD; a GET hit would then
                # replay the empty one.
                skip_reason = "only a GET response is stored"
            if skip_reason is not None:
                logger.debug("Not storing key=%s: %s", cache_key, skip_reason)

            # Update cache if needed
            elif not cached_data or cached_data.fingerprint != current_etag:
                assert (
                    current_body is not None
                )  # guaranteed by early-return guards above
                try:
                    await cache_backend.set(
                        cache_key,
                        _entry_for(current_response, current_etag, current_body),
                        ttl=ttl,
                    )
                except Exception as e:
                    if not fail_open:
                        raise
                    _log_backend_failure(
                        "write failed; response not stored", req, cache_key, e
                    )
                else:
                    logger.debug("Updated cache entry; key=%s ttl=%s", cache_key, ttl)

            # A client revalidating a copy that the fresh render reproduces gets
            # a 304, as on the bypass and no_cache paths: the entry may have
            # expired, been cleared or evicted, or never been stored in this
            # worker, while the client's ETag still matches (#237).
            not_modified = _fresh_not_modified(
                current_response,
                current_etag,
                client_etag,
                _cache_control_for(
                    current_response, response_cache_control, private_cache_control
                ),
            )
            if not_modified is not None:
                logger.debug("304 Not Modified (fresh render); key=%s", cache_key)
                return not_modified

            return _with_cache_control(
                current_response, response_cache_control, private_cache_control
            )

        @wraps(func)
        async def serve(*args: Any, **kwargs: Any) -> Response:
            if found_response:
                sub_response: Response | None = kwargs.get(response_name)
            else:
                sub_response = kwargs.pop(response_name, None)
            # The dependencies have run; the handler has not (see
            # `_split_header_lines`).
            dependency_lines = (
                [] if sub_response is None else list(sub_response.headers.raw)
            )
            dependency_status = (
                None if sub_response is None else sub_response.status_code
            )
            req: Request | None = kwargs.get(request_name)
            response = await respond(
                sub_response, dependency_lines, dependency_status, *args, **kwargs
            )
            return _with_dependency_headers(
                response,
                sub_response,
                dependency_lines,
                private_cache_control,
                cacheable_request=req is not None and req.method in _CACHED_METHODS,
            )

        wrapper: AsyncResponseCallable = serve
        if vary_names:

            @wraps(func)
            async def with_vary(*args: Any, **kwargs: Any) -> Response:
                # Read before `serve` pops an injected request parameter.
                req: Request | None = kwargs.get(request_name)
                response = await serve(*args, **kwargs)
                if req is not None and req.method in _CACHED_METHODS:
                    # Every GET or HEAD answer, served from the backend or not: a
                    # shared cache downstream keys on these headers too.
                    add_vary(response.headers, vary_names)
                return response

            wrapper = with_vary

        # Update the wrapper with the new signature
        update_wrapper(wrapper, func)
        wrapper.__signature__ = sig  # type: ignore[attr-defined]

        return wrapper

    return decorator
