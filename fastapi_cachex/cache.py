"""Core caching functionality and decorators."""

import inspect
import logging
import threading
import warnings
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
from functools import wraps
from inspect import Parameter
from inspect import Signature
from typing import Annotated
from typing import Any
from typing import Literal
from typing import NamedTuple
from typing import get_args
from typing import get_origin
from typing import get_type_hints
from urllib.parse import unquote
from urllib.parse import urlencode

from fastapi import Request
from fastapi import Response
from fastapi.params import Depends as DependsParam

from ._cache_control import _NO_STORE
from ._cache_control import CacheControl
from ._cache_control import _build_cache_control
from ._cache_control import _cache_control_for
from ._cache_control import _unshareable_reason
from ._cache_control import _with_cache_control
from ._coalesce import _Flight
from ._coalesce import _wait_for
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
from .backends.base import BaseCacheBackend
from .directives import DirectiveType
from .exceptions import BackendNotFoundError
from .exceptions import CacheXError
from .exceptions import RequestNotFoundError
from .exceptions import TTLTypeError
from .exceptions import TTLValueError
from .headers import add_vary
from .proxy import BackendProxy
from .proxy import get_backend_or_fallback
from .types import CacheEntry
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
    "invalidate_path",
    "logger",
]

logger = logging.getLogger(__name__)

# Methods `@cache` answers; HEAD shares the GET entry (#253).
_CACHED_METHODS = frozenset({"GET", "HEAD"})

# Sent as `X-Cache` with `debug_header=True` (#335).
_DEBUG_HEADER = "X-Cache"
_HIT = "HIT"
_MISS = "MISS"
_BYPASS = "BYPASS"


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


async def _read_entry(
    backend: BaseCacheBackend, cache_key: str, request: Request, *, fail_open: bool
) -> tuple[CacheEntry | None, bool]:
    """Read the entry under ``cache_key`` and whether the read worked.

    A failed read is a miss if failing open.
    """
    try:
        return await backend.get(cache_key), True
    except Exception as e:
        if not fail_open:
            raise
        _log_backend_failure("read failed; serving uncached", request, cache_key, e)
        return None, False


def _request_credential(request: Request) -> str | None:
    """What identifies the caller of ``request``, or ``None`` if nothing does.

    ``Authorization``, or a non-empty ``request.session`` from any session
    middleware (Starlette's ``SessionMiddleware``, for example). An empty
    session does not count, and neither does a ``Cookie`` header alone.
    """
    if "authorization" in request.headers:
        return "Authorization header"
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
    return True


async def invalidate_path(
    request: Request,
    path: str,
    key_builder: CacheKeyBuilder | None = None,
    vary: Sequence[str] | None = None,
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
        request: The current request. Only its host, scheme and headers are
            used.
        path: The path of the cached route as it appears in a URL, such as
            ``request.url_for("read_item", item_id=1).path`` returns: it is
            percent-decoded the way a server decodes a request path. In an
            app mounted under a prefix it is the full path, prefix included
            (which ``request.url_for`` gives, and ``app.url_path_for`` does
            not). It may carry a query string (``"/items?page=2"``).
        key_builder: As for ``invalidate()``.
        vary: As for ``invalidate()``.
        query: The query of the cached request, when it is not part of
            ``path``: a string (``"page=2"``), a mapping or a sequence of
            pairs, encoded as ``urllib.parse.urlencode`` does. Without one,
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
        "path": unquote(path),
        "raw_path": path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
    }
    return await invalidate(
        Request(scope, request.receive), key_builder, vary, sort_query=sort_query
    )


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


def _seconds(name: str, value: int | timedelta | None) -> int | None:
    """``value`` as a non-negative number of whole seconds, for ``@cache``.

    ``@cache`` allows ``0`` (``max-age=0``), which the backends refuse, so it
    does not use ``validate_ttl``; the type and range rules are the same.

    Raises:
        TTLTypeError: If ``value`` is not an ``int`` or a ``timedelta``
        TTLValueError: If ``value`` is negative, is larger than ``MAX_TTL`` or
            is a ``timedelta`` that is not a whole number of seconds

    Both are ``CacheXError`` subclasses, which ``@cache`` raised before, and
    the same classes the backends raise (#337).
    """
    if value is None:
        return None
    if isinstance(value, timedelta):
        if value.microseconds:
            msg = f"{name} must be a whole number of seconds, got {value!r}"
            raise TTLValueError(msg)
        value = int(value.total_seconds())
    elif isinstance(value, bool) or not isinstance(value, int):
        # Checked here: at request time the backend would reject it, and
        # failing open would hide that the route never caches.
        msg = (
            f"{name} must be an int number of seconds or a timedelta, "
            f"got {type(value).__name__}"
        )
        raise TTLTypeError(msg)
    if value < 0:
        msg = f"{name} must not be negative"
        raise TTLValueError(msg)
    if value > MAX_TTL:
        msg = f"{name} must be at most {MAX_TTL} seconds"
        raise TTLValueError(msg)
    return value


class _Params(NamedTuple):
    """The wrapper's signature and where its ``Request`` and ``Response`` are.

    ``*_injected`` says the parameter was added by ``@cache``: the wrapper
    then takes it out of the keyword arguments before calling the handler.
    """

    signature: Signature
    request_name: str
    request_injected: bool
    response_name: str
    response_injected: bool

    def take_request(self, kwargs: dict[str, Any]) -> Request | None:
        request: Request | None = (
            kwargs.pop(self.request_name, None)
            if self.request_injected
            else kwargs.get(self.request_name)
        )
        return request

    def take_response(self, kwargs: dict[str, Any]) -> Response | None:
        response: Response | None = (
            kwargs.pop(self.response_name, None)
            if self.response_injected
            else kwargs.get(self.response_name)
        )
        return response


def _wrapper_params(func: HandlerCallable) -> _Params:
    """Find the handler's ``Request`` and ``Response`` parameters, or add them.

    The wrapper needs the request, and FastAPI's sub-response even when the
    handler does not ask for it, to send what the dependencies set on it
    (#233).
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    found_request = _find_request_param(func, params)
    found_response = _find_response_param(func, params)

    injected = [
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation)
        for found, name, annotation in (
            (found_request, "__cachex_request", Request),
            (found_response, "__cachex_response", Response),
        )
        if found is None
    ]
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
    return _Params(
        signature=sig,
        request_name=found_request.name if found_request else "__cachex_request",
        request_injected=found_request is None,
        response_name=found_response.name if found_response else "__cachex_response",
        response_injected=found_response is None,
    )


@dataclass
class _Call:
    """One GET, HEAD or other request to a decorated handler.

    ``dependency_lines`` and ``dependency_status`` are what the dependencies
    left on the sub-response before the handler ran (see
    ``_split_header_lines``). ``outcome`` is how the request was answered,
    for the ``X-Cache`` header: ``HIT`` (served from the stored entry, as a
    200 or a 304 for its ETag), ``MISS`` (the backend was consulted and the
    handler ran), ``BYPASS`` (the backend was neither read nor written), or
    ``None`` for a method that is not cached.
    """

    request: Request
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    sub_response: Response | None
    dependency_lines: list[tuple[bytes, bytes]]
    dependency_status: int | None
    flight: _Flight = field(default_factory=_Flight)
    outcome: str | None = None

    @property
    def client_etag(self) -> str | None:
        return self.request.headers.get("if-none-match")


@dataclass(frozen=True, eq=False)
class _Route:
    """What ``@cache(...)`` decided for one handler, and how it answers.

    ``cache_control`` only depends on the decorator arguments, so it is
    built once. ``private_cache_control`` is sent instead for a response
    that must not be stored downstream either: one that sets a cookie, or
    one answering a request with credentials.
    """

    func: HandlerCallable
    ttl: int | None
    no_cache: bool
    no_store: bool
    public: bool
    private: bool
    fail_open: bool
    cache_authorized: bool
    coalesce: bool
    key_builder: CacheKeyBuilder
    vary_names: list[str]
    cache_control: str
    private_cache_control: str
    warn_bypass: _BypassWarner

    @property
    def skips_backend(self) -> bool:
        """Whether no request to this route reads or writes the backend.

        Without a positive ttl nothing may be served from storage, and a 304
        answered from a stored ETag would be exactly that: it would keep
        confirming a copy that the handler no longer produces (#110). Such
        routes skip the backend like private ones. ``ttl=0`` is included,
        since ``max-age=0`` allows no reuse either.
        """
        return self.private or not self.ttl

    async def respond(self, call: _Call, backend: BaseCacheBackend) -> Response:
        """Answer ``call``, from the backend where the route and request allow."""
        req = call.request
        if req.method not in _CACHED_METHODS:
            logger.debug("Not GET or HEAD; bypassing cache for method=%s", req.method)
            return await self._run(call)
        if self.no_store:
            return await self._no_store(call)

        # RFC 9111 §3.5: a shared cache must not reuse a response to a
        # request with `Authorization` unless the response allows it. The
        # default key carries no identity, so treat such requests as
        # private unless the route is `public` or opted in. A request with
        # a non-empty `request.session` is the same case (#319). Routes
        # without a positive ttl skip the backend anyway, but their
        # response still needs `private` for a downstream cache (#362);
        # only `private=True` already sends it.
        # `cache_authorized` lifts the bypass but not `private`: its entries
        # are per caller only in this backend, while a shared cache
        # downstream keys on the URL alone (#372).
        credential = None if self.private or self.public else _request_credential(req)
        cache_control = (
            self.cache_control if credential is None else self.private_cache_control
        )
        authorized_bypass = credential is not None and not self.cache_authorized
        if credential is not None and not self.cache_authorized:
            logger.debug(
                "%s present; bypassing the backend for path=%s",
                credential,
                req.url.path,
            )
            # The warning is about lost cache hits, which a route that
            # never reads the backend does not have.
            if not self.skips_backend:
                self.warn_bypass(req, credential)

        if self.skips_backend or authorized_bypass:
            return await self._uncached(call, cache_control)
        return await self._cached(call, backend, cache_control)

    async def _run(self, call: _Call) -> Response:
        """Run the handler and build its response."""
        return await _respond(
            self.func,
            call.request,
            call.args,
            call.kwargs,
            sub_response=call.sub_response,
            dependency_lines=call.dependency_lines,
        )

    async def _render(self, call: _Call) -> tuple[Response, bytes | None, str | None]:
        """Run the handler; the body and ETag are None for a streamed response."""
        return await _render(
            self.func,
            call.request,
            call.args,
            call.kwargs,
            sub_response=call.sub_response,
            dependency_lines=call.dependency_lines,
        )

    def _send(self, response: Response, cache_control: str) -> Response:
        """A freshly rendered response, with the ``Cache-Control`` it gets."""
        return _with_cache_control(response, cache_control, self.private_cache_control)

    def _revalidated(
        self, call: _Call, response: Response, etag: str, cache_control: str
    ) -> Response | None:
        """The 304 for a fresh render the client's ETag matches, else None."""
        return _fresh_not_modified(
            response,
            etag,
            call.client_etag,
            _cache_control_for(response, cache_control, self.private_cache_control),
        )

    async def _no_store(self, call: _Call) -> Response:
        """``no_store=True``: the highest priority, nothing else applies."""
        call.outcome = _BYPASS
        response = await self._run(call)
        logger.debug(
            "no-store active; bypassed cache for path=%s", call.request.url.path
        )
        # Unconditional: `no-store` is stricter than anything the
        # handler may have sent.
        response.headers["Cache-Control"] = _NO_STORE
        return response

    async def _uncached(self, call: _Call, cache_control: str) -> Response:
        """Answer without reading or writing the backend.

        A private response belongs to exactly one user, so it must never
        be read from or written to the shared backend: the default cache
        key carries no identity, so a stored copy would be served to the
        next caller. The same path serves routes without a positive ttl
        (see ``skips_backend``). ETag revalidation still works: it compares
        the client's validator against freshly rendered content.

        ``cache_control`` has ``private`` for a request with credentials:
        RFC 9111 §3.5 would still let a downstream shared cache reuse the
        answer to an ``Authorization`` request under ``must-revalidate``,
        and nothing at all stops one reusing the answer to a cookie.
        """
        call.outcome = _BYPASS
        path = call.request.url.path
        response, _, etag = await self._render(call)
        if not _is_cacheable_status(response.status_code):
            return response
        if etag is None:
            # StreamingResponse/FileResponse — cannot compute ETag
            return self._send(response, cache_control)
        not_modified = self._revalidated(call, response, etag, cache_control)
        if not_modified is not None:
            logger.debug("304 Not Modified (uncached); path=%s", path)
            return not_modified
        response.headers["ETag"] = etag
        logger.debug("Bypassed the backend; path=%s", path)
        return self._send(response, cache_control)

    async def _cached(
        self, call: _Call, backend: BaseCacheBackend, cache_control: str
    ) -> Response:
        """Answer from the stored entry, or render, store and answer."""
        req = call.request
        # Built only here: the paths above never touch the backend, so a
        # custom key builder would run for nothing.
        # HEAD reads the entry of the GET (#253), whichever builder made
        # the key.
        cache_key = _append_key_components(
            _build_key(self.key_builder, _as_get(req) if req.method == "HEAD" else req),
            _vary_components(req, self.vary_names),
        )

        # Decided below: a hit is served from the entry, anything else runs
        # the handler.
        call.outcome = _MISS
        entry = await self._read(call, backend, cache_key)
        if entry is not None and not self.no_cache:
            return self._hit(call, entry, cache_key, cache_control)

        response, body, etag = await self._render(call)
        if not _is_cacheable_status(response.status_code):
            # Leave any existing entry alone: a transient error must not
            # evict or overwrite the last good response. Error responses
            # carry no validator either: never answer 304.
            logger.debug(
                "Uncacheable status %s; not storing key=%s",
                response.status_code,
                cache_key,
            )
            return response
        if body is None or etag is None:
            # StreamingResponse/FileResponse — cannot compute ETag; serve as-is
            return self._send(response, cache_control)

        if self.no_cache and call.client_etag:
            # A `no_cache` route answers a matching ETag before storing, so
            # that 304 leaves the stored entry as it is.
            not_modified = self._revalidated(call, response, etag, cache_control)
            if not_modified is not None:
                logger.debug("304 Not Modified via no-cache; key=%s", cache_key)
                return not_modified
        else:
            logger.debug("Cache MISS; computed fresh ETag for key=%s", cache_key)

        response.headers["ETag"] = etag
        await self._store(
            call,
            backend,
            cache_key,
            stored=entry,
            response=response,
            etag=etag,
            body=body,
        )

        # A client revalidating a copy that the fresh render reproduces gets
        # a 304, as on the bypass and no_cache paths: the entry may have
        # expired, been cleared or evicted, or never been stored in this
        # worker, while the client's ETag still matches (#237).
        not_modified = self._revalidated(call, response, etag, cache_control)
        if not_modified is not None:
            logger.debug("304 Not Modified (fresh render); key=%s", cache_key)
            return not_modified
        return self._send(response, cache_control)

    async def _read(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> CacheEntry | None:
        """The stored entry, after waiting for a running miss if coalescing."""
        req = call.request
        entry, read_ok = await _read_entry(
            backend, cache_key, req, fail_open=self.fail_open
        )
        # A failed read does not wait: the backend is likely down, so the
        # leader could not store its response either. A miss read just
        # before a leader stores may still lead a second render; that
        # window is one backend round trip.
        if self.coalesce and read_ok and entry is None:
            leader = call.flight.join(backend, cache_key, may_lead=req.method == "GET")
            if leader is not None:
                logger.debug("Waiting for a running miss; key=%s", cache_key)
                await _wait_for(leader)
                entry, _ = await _read_entry(
                    backend, cache_key, req, fail_open=self.fail_open
                )
        return entry

    def _hit(
        self, call: _Call, entry: CacheEntry, cache_key: str, cache_control: str
    ) -> Response:
        """Serve the stored entry: a 304 for its ETag, or the entry itself."""
        call.outcome = _HIT
        if _etag_matches(call.client_etag, entry.fingerprint):
            logger.debug("304 Not Modified (cached ETag match); key=%s", cache_key)
            # Answered from the stored entry, so the 304 says how old
            # that entry is: a cache refreshing its copy with this 304
            # takes the new Age with it (RFC 9111 §4.3.4).
            return _not_modified(
                entry.fingerprint,
                cache_control,
                entry.headers,
                _age_headers(entry, self.ttl),
            )

        logger.debug("Cache HIT (TTL valid); key=%s", cache_key)
        hit = Response(
            content=entry.content,
            status_code=entry.status_code,
            media_type=entry.media_type,
        )
        # Framing and validator headers come from this response, not
        # the entry: an entry built outside @cache may still carry them.
        _append_headers(
            hit,
            (
                (name, value)
                for name, value in entry.headers
                if name.lower() not in _UNCACHEABLE_HEADERS
            ),
        )
        hit.headers["ETag"] = entry.fingerprint
        hit.headers["Cache-Control"] = cache_control
        hit.headers.update(_age_headers(entry, self.ttl))
        return hit

    def _skip_reason(self, call: _Call, response: Response) -> str | None:
        """Why a rendered response is served but not stored, if it is not.

        A response scoped to one caller is never stored. An entry already
        under its key is left alone, as for an error status: it came from a
        response that was shareable.
        """
        reason = _unshareable_reason(response)
        if reason is None:
            reason = _dependency_unshareable_reason(call.dependency_lines)
        if reason is None and call.dependency_status is not None:
            # It may hold for this request only, and a hit would replay it.
            reason = "a dependency set the status code"
        if reason is None and call.request.method == "HEAD":
            # A handler may skip the body for HEAD; a GET hit would then
            # replay the empty one.
            reason = "only a GET response is stored"
        return reason

    async def _store(
        self,
        call: _Call,
        backend: BaseCacheBackend,
        cache_key: str,
        *,
        stored: CacheEntry | None,
        response: Response,
        etag: str,
        body: bytes,
    ) -> None:
        """Store a rendered response, unless it must not be or already is."""
        skip_reason = self._skip_reason(call, response)
        if skip_reason is not None:
            logger.debug("Not storing key=%s: %s", cache_key, skip_reason)
            return
        if stored is not None and stored.fingerprint == etag:
            return
        try:
            await backend.set(cache_key, _entry_for(response, etag, body), ttl=self.ttl)
        except Exception as e:
            if not self.fail_open:
                raise
            _log_backend_failure(
                "write failed; response not stored", call.request, cache_key, e
            )
        else:
            logger.debug("Updated cache entry; key=%s ttl=%s", cache_key, self.ttl)


def _check_arguments(
    *,
    ttl: int | None,
    stale: object,
    stale_ttl: int | None,
    no_cache: bool,
    no_store: bool,
    public: bool,
    private: bool,
    coalesce: object,
    debug_header: object,
    s_maxage: int | None,
    proxy_revalidate: bool,
) -> None:
    """Reject ``@cache`` arguments that contradict each other.

    Raises:
        CacheXError: See ``cache()``
    """
    if stale is not None and stale_ttl is None:
        msg = "stale_ttl must be set if stale is used"
        raise CacheXError(msg)
    if stale_ttl is not None and stale is None:
        msg = "stale must be set if stale_ttl is used"
        raise CacheXError(msg)
    if public and private:
        msg = "public and private are mutually exclusive"
        raise CacheXError(msg)
    for name, value in (("s_maxage", s_maxage), ("proxy_revalidate", proxy_revalidate)):
        # Identity, not equality: `s_maxage=0` is a value, and `0 == False`.
        if private and value is not None and value is not False:
            # Both speak to shared caches, which must not store a private
            # response at all.
            msg = (
                f"{name} cannot be combined with private: it only applies "
                "to shared caches, which do not store a private response"
            )
            raise CacheXError(msg)
    if not isinstance(coalesce, bool):
        msg = f"coalesce must be a bool, got {type(coalesce).__name__}"
        raise CacheXError(msg)
    if not isinstance(debug_header, bool):
        msg = f"debug_header must be a bool, got {type(debug_header).__name__}"
        raise CacheXError(msg)
    if coalesce and not no_store and (private or no_cache or not ttl):
        # Such a route never serves a stored entry, so there would be
        # nothing to wait for.
        msg = (
            "coalesce needs a positive ttl and cannot be combined with "
            "private or no_cache, which never serve a stored entry"
        )
        raise CacheXError(msg)


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
    vary: Sequence[str] | None = None,
    sort_query: bool | None = None,
    coalesce: bool = False,
    debug_header: bool = False,
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
            by ``stale``. It only shapes the ``Cache-Control`` header; the
            backend entry still expires after ``ttl``. Must be given together
            with ``stale``.
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
            header multiplies the number of entries, so normalise them in a
            ``key_builder`` when only a few values matter. The credential
            headers ``Authorization``, ``Proxy-Authorization``, ``Cookie``
            and ``X-Session-Token`` are keyed on ``sha256:<hex digest>`` of
            the value rather than the value, so no token or session cookie
            appears in the key; missing or empty, they stay ``name=``.
            Listing ``Cookie`` emits a ``UserWarning`` when the decorator is
            applied, since every visitor then gets their own entry.
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
            ``X-Cache`` the handler set itself is replaced.

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
            ``coalesce`` is not a ``bool`` or is set without a positive
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
            s_maxage=s_maxage,
            proxy_revalidate=proxy_revalidate,
        )
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
                    ("s_maxage", s_maxage),
                    ("proxy_revalidate", proxy_revalidate),
                    ("no_transform", no_transform),
                    ("coalesce", coalesce),
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
            no_cache=no_cache,
            no_store=no_store,
            public=public,
            private=private,
            fail_open=fail_open,
            cache_authorized=cache_authorized,
            coalesce=coalesce,
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
        params = _wrapper_params(func)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Response:
            sub_response = params.take_response(kwargs)
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
            )
            try:
                response = await route.respond(call, backend)
            finally:
                # Releases the requests waiting on this one (#252).
                call.flight.finish()
            if debug_header and call.outcome is not None:
                # Set after the entry was built, so it is never stored.
                response.headers[_DEBUG_HEADER] = call.outcome
            cacheable_request = request.method in _CACHED_METHODS
            response = _with_dependency_headers(
                response,
                sub_response,
                call.dependency_lines,
                route.private_cache_control,
                cacheable_request=cacheable_request,
            )
            if vary_names and cacheable_request:
                # Every GET or HEAD answer, served from the backend or not: a
                # shared cache downstream keys on these headers too.
                add_vary(response.headers, vary_names)
            return response

        wrapper.__signature__ = params.signature  # type: ignore[attr-defined]
        return wrapper

    return decorator
