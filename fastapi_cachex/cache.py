"""Core caching functionality and decorators."""

import inspect
import logging
import threading
import time
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
from urllib.parse import urlencode

from fastapi import BackgroundTasks
from fastapi import Request
from fastapi import Response
from fastapi.params import Depends as DependsParam
from starlette.background import BackgroundTasks as StarletteBackgroundTasks
from starlette.exceptions import HTTPException as StarletteHTTPException

from ._cache_control import _NO_STORE
from ._cache_control import CacheControl
from ._cache_control import _build_cache_control
from ._cache_control import _cache_control_for
from ._cache_control import _unshareable_reason
from ._cache_control import _with_cache_control
from ._callables import _is_coroutine_callable
from ._coalesce import _claim_refresh
from ._coalesce import _Flight
from ._coalesce import _release_refresh
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
from ._stored_response import _entry_age
from ._stored_response import _entry_for
from ._stored_response import _etag_matches
from ._stored_response import _fresh_not_modified
from ._stored_response import _is_cacheable_status
from ._stored_response import _not_modified
from ._vary import Vary
from ._vary import VaryNormaliser
from ._vary import _validate_vary
from ._vary import _vary_components
from .backends.base import MAX_TTL
from .backends.base import BaseCacheBackend
from .directives import DirectiveType
from .events import _emit
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
_REFRESH = "REFRESH"
_STALE = "STALE"

# From here up a response is a failure that `stale="error"` may answer from a
# stale entry (RFC 5861 §4 names 500, 502, 503 and 504).
_SERVER_ERROR = 500

# What a request's own `Cache-Control` may ask for on a route that allows it
# (`client_cache_control`, #541).
_ClientCacheControl = bool | Callable[[Request], bool]


def _client_directive(request: Request) -> str | None:
    """``"no-store"``, ``"no-cache"`` or None, from the request's ``Cache-Control``.

    ``max-age=0`` asks for the same as ``no-cache``: a response no older than
    now. Every other request directive is ignored.
    """
    directives = {
        directive.strip().lower().replace(" ", "")
        # Every line of the header: a client or a proxy may send several.
        for line in request.headers.getlist("cache-control")
        for directive in line.split(",")
    }
    if "no-store" in directives:
        return "no-store"
    if directives & {"no-cache", "max-age=0"}:
        return "no-cache"
    return None


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
    _emit("error", "http", cache_key, route=_route_template(request))


def _route_template(request: Request) -> str | None:
    """The path template of the route answering ``request``, if it has one.

    The whole path, as a label that does not grow with the data:
    ``/api/users/{user_id}``. The matched route only knows its own part when
    it was included with a prefix or lives in a mounted application, so what
    precedes that part in the request's path is put back in front of it, with
    the values of any path parameters there replaced by their names.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    path_format = getattr(route, "path_format", None)
    if not isinstance(template, str) or not isinstance(path_format, str):
        return None
    params = request.path_params
    try:
        own = path_format.format(**params)
    except (KeyError, IndexError, ValueError):
        return template
    path = request.url.path
    if own == path or not path.endswith(own):
        return template
    prefix = path[: len(path) - len(own)]
    for name, value in params.items():
        if f"{{{name}" not in path_format:
            prefix = prefix.replace(f"/{value}", f"/{{{name}}}", 1)
    return prefix + template


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


def _is_background_annotation(annotation: Any) -> bool:
    """Whether an annotation asks for the request's ``BackgroundTasks``."""
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    # Starlette's class: FastAPI fills a parameter annotated with either.
    return isinstance(annotation, type) and issubclass(
        annotation, StarletteBackgroundTasks
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


_BACKGROUND_PARAM = "__cachex_background"


class _Params(NamedTuple):
    """The wrapper's signature and where its ``Request`` and ``Response`` are.

    ``background_name`` is the ``BackgroundTasks`` parameter added for a route
    that refreshes stale entries after the response (``serve_stale``), or None.

    ``*_injected`` says the parameter was added by ``@cache``: the wrapper
    then takes it out of the keyword arguments before calling the handler.
    """

    signature: Signature
    request_name: str
    request_injected: bool
    response_name: str
    response_injected: bool
    background_name: str | None = None
    background_injected: bool = False

    def take_background(self, kwargs: dict[str, Any]) -> BackgroundTasks | None:
        if self.background_name is None:
            return None
        background: BackgroundTasks | None = (
            kwargs.pop(self.background_name, None)
            if self.background_injected
            else kwargs.get(self.background_name)
        )
        return background

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


def _wrapper_params(func: HandlerCallable, *, background: bool = False) -> _Params:
    """Find the handler's ``Request`` and ``Response`` parameters, or add them.

    The wrapper needs the request, and FastAPI's sub-response even when the
    handler does not ask for it, to send what the dependencies set on it
    (#233). With ``background`` it also asks for the request's
    ``BackgroundTasks``, the same object FastAPI hands to the handler and the
    dependencies, to run the handler after a stale response was sent (#539).
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    found_request = _find_request_param(func, params)
    found_response = _find_response_param(func, params)
    # FastAPI fills one BackgroundTasks parameter per function: the handler's
    # own is used when it has one.
    found_background = (
        _find_param(func, params, _is_background_annotation) if background else ...
    )

    injected = [
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation)
        for found, name, annotation in (
            (found_request, "__cachex_request", Request),
            (found_response, "__cachex_response", Response),
            (found_background, _BACKGROUND_PARAM, BackgroundTasks),
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
        background_name=(
            None
            if not background
            else getattr(found_background, "name", _BACKGROUND_PARAM)
        ),
        background_injected=found_background is None,
    )


@dataclass
class _Call:
    """One GET, HEAD or other request to a decorated handler.

    ``dependency_lines`` and ``dependency_status`` are what the dependencies
    left on the sub-response before the handler ran (see
    ``_split_header_lines``). ``outcome`` is how the request was answered,
    for the ``X-Cache`` header: ``HIT`` (served from the stored entry, as a
    200 or a 304 for its ETag), ``MISS`` (the backend was consulted and the
    handler ran), ``BYPASS`` (the backend was neither read nor written),
    ``REFRESH`` (the request skipped the entry, see ``client_cache_control``),
    ``STALE`` (an entry past its ttl was served, see ``serve_stale``), or
    ``None`` for a method that is not cached.
    """

    request: Request
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    sub_response: Response | None
    dependency_lines: list[tuple[bytes, bytes]]
    dependency_status: int | None
    background: BackgroundTasks | None = None
    cache_key: str | None = None
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
    stale: Literal["error", "revalidate"] | None
    stale_ttl: int | None
    serve_stale: bool
    no_cache: bool
    no_store: bool
    public: bool
    private: bool
    fail_open: bool
    cache_authorized: bool
    coalesce: bool
    client_cache_control: _ClientCacheControl
    key_builder: CacheKeyBuilder
    vary_names: dict[str, VaryNormaliser | None]
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

    @property
    def stored_ttl(self) -> int | None:
        """How long the backend keeps an entry.

        With ``serve_stale`` it outlives ``ttl`` by ``stale_ttl``, the time
        in which it may still be served stale.
        """
        if self.serve_stale and self.ttl is not None and self.stale_ttl is not None:
            return self.ttl + self.stale_ttl
        return self.ttl

    def _is_stale(self, entry: CacheEntry) -> bool:
        """Whether ``entry`` is past its ttl and kept only to be served stale.

        Without ``serve_stale`` the backend drops an entry at its ttl, so one
        that is read is fresh. An entry without ``stored_at`` (written by an
        older release, or outside ``@cache``) is taken as fresh.
        """
        if not self.serve_stale or self.ttl is None:
            return False
        age = _entry_age(entry)
        return age is not None and age >= self.ttl

    def _is_gone(self, entry: CacheEntry) -> bool:
        """Whether a stale ``entry`` is past the time it may be served at all.

        The backend drops it by then on its own clock; this one is the clock
        ``stored_at`` was written with, which may be another host's.
        """
        age = _entry_age(entry)
        return (
            self.serve_stale
            and age is not None
            and self.stored_ttl is not None
            and age >= self.stored_ttl
        )

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
        asked = self._client_asks(req)
        if asked == "no-store":
            logger.debug("Request no-store; bypassing for path=%s", req.url.path)
            return await self._uncached(call, cache_control)
        return await self._cached(
            call, backend, cache_control, refresh=asked == "no-cache"
        )

    def _client_asks(self, request: Request) -> str | None:
        """What the request's ``Cache-Control`` asks for, if this route listens."""
        allowed = self.client_cache_control
        if allowed is False:
            return None
        asked = _client_directive(request)
        # `is True`: anything else a callable returns, a coroutine or a
        # non-empty string included, must not open the route.
        if asked is None or (allowed is not True and allowed(request) is not True):
            return None
        return asked

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
        self,
        call: _Call,
        backend: BaseCacheBackend,
        cache_control: str,
        *,
        refresh: bool = False,
    ) -> Response:
        """Answer from the stored entry, or render, store and answer.

        ``refresh`` skips the stored entry: the client asked for a response
        that is not from the cache, and the route allows that. The new
        response replaces the entry.
        """
        req = call.request
        # Built only here: the paths above never touch the backend, so a
        # custom key builder would run for nothing.
        # HEAD reads the entry of the GET (#253), whichever builder made
        # the key.
        cache_key = _append_key_components(
            _build_key(self.key_builder, _as_get(req) if req.method == "HEAD" else req),
            _vary_components(req, self.vary_names),
        )

        call.cache_key = cache_key
        # Decided below: a hit is served from the entry, anything else runs
        # the handler.
        call.outcome = _REFRESH if refresh else _MISS
        if refresh:
            entry = None
            await self._after_running_miss(call, backend, cache_key)
        else:
            entry = await self._read(call, backend, cache_key)
        if entry is not None and self._is_gone(entry):
            entry = None
        stale = entry is not None and self._is_stale(entry)
        if entry is not None and not self.no_cache and not stale:
            return self._hit(call, entry, cache_key, cache_control)
        if (
            entry is not None
            and stale
            and self.stale == "revalidate"
            and self._refresh_later(call, backend, cache_key)
        ):
            # Answer now, from the old entry; the handler runs afterwards.
            return self._hit(call, entry, cache_key, cache_control, outcome=_STALE)

        fallback = entry if stale and self.stale == "error" else None
        rendered = await self._render_unless_failing(call, fallback)
        if rendered is None:
            # Only with a fallback: the handler failed, the stale entry answers.
            assert fallback is not None  # noqa: S101 - for the type checker
            return self._hit(call, fallback, cache_key, cache_control, outcome=_STALE)
        return await self._fresh(
            call, backend, cache_key, cache_control, stored=entry, rendered=rendered
        )

    async def _fresh(
        self,
        call: _Call,
        backend: BaseCacheBackend,
        cache_key: str,
        cache_control: str,
        *,
        stored: CacheEntry | None,
        rendered: tuple[Response, bytes | None, str | None],
    ) -> Response:
        """Store what the handler just rendered, and answer with it."""
        entry = stored
        response, body, etag = rendered
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

    def _refresh_later(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> bool:
        """Arrange for a stale entry to be replaced after this response.

        A GET adds the refresh to the request's background tasks; a HEAD is
        just answered, since its response is never stored. False only when
        the request has no ``BackgroundTasks`` to run it on (the wrapper was
        not called by FastAPI), in which case the caller renders at once.
        """
        if call.background is None:
            return False
        if call.request.method == "GET":
            call.background.add_task(self._refresh, call, backend, cache_key)
        return True

    async def _refresh(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> None:
        """Replace a stale entry, after the response it answered was sent.

        A background task of the request: its dependencies are still open.
        One refresh per key and process runs at a time. Nothing is left to
        answer, so a failure is logged and the stale entry stays until a
        later request refreshes it or it expires.
        """
        if not _claim_refresh(backend, cache_key):
            return
        try:
            await self._replace_stale(call, backend, cache_key)
        except Exception as e:  # noqa: BLE001 - there is no response to fail
            logger.warning(
                "Refreshing the stale entry failed (%s: %s); path=%s",
                type(e).__name__,
                e,
                call.request.url.path,
            )
        finally:
            _release_refresh(backend, cache_key)

    async def _replace_stale(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> None:
        """Run the handler and store its response, unless the entry is fresh."""
        entry, _ = await _read_entry(backend, cache_key, call.request, fail_open=True)
        if entry is not None and not self._is_stale(entry):
            # A request served stale just before this one already replaced it.
            return
        response, body, etag = await self._render(call)
        reason = self._skip_reason(call, response)
        if (
            body is None
            or etag is None
            or not _is_cacheable_status(response.status_code)
        ):
            reason = f"status {response.status_code}, or a streamed body"
        if reason is None:
            await self._store(
                call,
                backend,
                cache_key,
                stored=None,
                response=response,
                etag=etag,  # type: ignore[arg-type]
                body=body,  # type: ignore[arg-type]
            )
        else:
            logger.warning(
                "Refresh not stored (%s); the stale entry stays; path=%s",
                reason,
                call.request.url.path,
            )
        if response.background is not None:
            # The handler's own task: nothing sends this response to run it.
            await response.background()

    def _is_failure(self, request: Request, error: Exception) -> bool:
        """Whether ``error`` is a failure a stale entry may answer for.

        An ``HTTPException`` below 500 is the handler's answer. So is an
        exception the application has registered a handler for: it turns it
        into a response of its own choosing, often a 4xx.
        """
        if isinstance(error, StarletteHTTPException):
            return error.status_code >= _SERVER_ERROR
        handlers = getattr(request.app, "exception_handlers", {})
        return not any(
            cls in handlers for cls in type(error).__mro__ if cls is not Exception
        )

    async def _render_unless_failing(
        self, call: _Call, fallback: CacheEntry | None
    ) -> tuple[Response, bytes | None, str | None] | None:
        """Run the handler; None if it failed and ``fallback`` can answer.

        ``fallback`` is the stale entry of a ``stale="error"`` route. A
        failure is a 5xx response or an exception, other than the ones
        ``_is_failure`` takes as the handler's answer.
        """
        try:
            rendered = await self._render(call)
        except Exception as e:
            if fallback is None or not self._is_failure(call.request, e):
                raise
            # One line, no traceback: a failing upstream fails every request.
            logger.warning(
                "Handler failed (%s: %s); serving the stale entry; path=%s",
                type(e).__name__,
                e,
                call.request.url.path,
            )
            return None
        if fallback is not None and rendered[0].status_code >= _SERVER_ERROR:
            logger.warning(
                "Handler answered %s; serving the stale entry; path=%s",
                rendered[0].status_code,
                call.request.url.path,
            )
            return None
        return rendered

    async def _after_running_miss(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> None:
        """Let a coalesced miss that is running for the key finish first.

        A refresh renders after it, so the newer response is the one left in
        the backend. Without a running miss a GET leads, and misses arriving
        meanwhile wait for what it stores.
        """
        if not self.coalesce:
            return
        leader = call.flight.join(
            backend, cache_key, may_lead=call.request.method == "GET"
        )
        if leader is not None:
            await _wait_for(leader)

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
        # A stale entry that only answers for a failing handler is waited
        # for like a miss: the leader's render may make it fresh.
        awaited = entry is None or (
            self.stale == "error" and self._is_stale(entry) and not self._is_gone(entry)
        )
        if self.coalesce and read_ok and awaited:
            leader = call.flight.join(backend, cache_key, may_lead=req.method == "GET")
            if leader is not None:
                logger.debug("Waiting for a running miss; key=%s", cache_key)
                await _wait_for(leader)
                entry, _ = await _read_entry(
                    backend, cache_key, req, fail_open=self.fail_open
                )
        return entry

    def _hit(
        self,
        call: _Call,
        entry: CacheEntry,
        cache_key: str,
        cache_control: str,
        *,
        outcome: str = _HIT,
    ) -> Response:
        """Serve the stored entry: a 304 for its ETag, or the entry itself."""
        call.outcome = outcome
        if _etag_matches(call.client_etag, entry.fingerprint):
            logger.debug("304 Not Modified (cached ETag match); key=%s", cache_key)
            # Answered from the stored entry, so the 304 says how old
            # that entry is: a cache refreshing its copy with this 304
            # takes the new Age with it (RFC 9111 §4.3.4).
            return _not_modified(
                entry.fingerprint,
                cache_control,
                entry.headers,
                _age_headers(entry, self.stored_ttl),
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
        hit.headers.update(_age_headers(entry, self.stored_ttl))
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
        if (
            stored is not None
            and stored.fingerprint == etag
            and not self._is_stale(stored)
        ):
            return
        try:
            await backend.set(
                cache_key, _entry_for(response, etag, body), ttl=self.stored_ttl
            )
        except Exception as e:
            if not self.fail_open:
                raise
            _log_backend_failure(
                "write failed; response not stored", call.request, cache_key, e
            )
        else:
            logger.debug(
                "Updated cache entry; key=%s ttl=%s", cache_key, self.stored_ttl
            )
            _emit("store", "http", cache_key, route=_route_template(call.request))


def _check_client_cache_control(value: object) -> None:
    """Reject a ``client_cache_control`` that is not a bool or a plain function."""
    if isinstance(value, bool):
        return
    if not callable(value):
        msg = (
            "client_cache_control must be a bool or a callable taking the "
            f"request, got {type(value).__name__}"
        )
        raise CacheXError(msg)
    if _is_coroutine_callable(value):
        # Its coroutine would never be awaited, and would count as "yes".
        msg = "client_cache_control must be a plain function, not async"
        raise CacheXError(msg)


def _check_serve_stale(
    serve_stale: object,
    *,
    ttl: int | None,
    stale: object,
    no_cache: bool,
    private: bool,
    must_revalidate: bool,
) -> None:
    """Reject a ``serve_stale`` route that could never serve a stale entry."""
    if not isinstance(serve_stale, bool):
        msg = f"serve_stale must be a bool, got {type(serve_stale).__name__}"
        raise CacheXError(msg)
    if not serve_stale:
        return
    if stale is None:
        msg = "serve_stale needs stale and stale_ttl: they say when and how long"
        raise CacheXError(msg)
    if private or no_cache or must_revalidate or not ttl:
        msg = (
            "serve_stale needs a positive ttl and cannot be combined with "
            "private, no_cache or must_revalidate, which never allow a stale "
            "response"
        )
        raise CacheXError(msg)


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
    client_cache_control: object,
    serve_stale: object,
    must_revalidate: bool,
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
    _check_client_cache_control(client_cache_control)
    if not no_store:
        _check_serve_stale(
            serve_stale,
            ttl=ttl,
            stale=stale,
            no_cache=no_cache,
            private=private,
            must_revalidate=must_revalidate,
        )
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
    vary: Vary | None = None,
    sort_query: bool | None = None,
    coalesce: bool = False,
    debug_header: bool = False,
    client_cache_control: _ClientCacheControl = False,
    serve_stale: bool = False,
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
            ``X-Cache`` the handler set itself is replaced. ``REFRESH``
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
            client_cache_control=client_cache_control,
            serve_stale=serve_stale,
            must_revalidate=must_revalidate,
            s_maxage=s_maxage,
            proxy_revalidate=proxy_revalidate,
        )
        vary_names = _validate_vary(vary)
        builder = _resolve_key_builder(key_builder, sort_query)
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
                # Releases the requests waiting on this one (#252).
                call.flight.finish()
                # Also when the handler raised: that request was a miss or a
                # bypass all the same.
                if call.outcome is not None:
                    _emit(
                        call.outcome.lower(),  # type: ignore[arg-type]
                        "http",
                        call.cache_key,
                        route=_route_template(request),
                        duration=time.perf_counter() - started,
                    )
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
                add_vary(response.headers, list(vary_names))
            return response

        wrapper.__signature__ = params.signature  # type: ignore[attr-defined]
        return wrapper

    return decorator
