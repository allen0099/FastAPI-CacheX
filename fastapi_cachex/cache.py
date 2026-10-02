"""Core caching functionality and decorators."""

import hashlib
import inspect
import logging
import threading
import time
import warnings
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from functools import partial
from functools import update_wrapper
from functools import wraps
from inspect import Parameter
from inspect import Signature
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from typing import Literal
from typing import cast
from typing import get_args
from typing import get_origin
from typing import get_type_hints

from fastapi import Request
from fastapi import Response
from fastapi.encoders import jsonable_encoder
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import TypeAdapter
from starlette.concurrency import run_in_threadpool
from starlette.status import HTTP_200_OK
from starlette.status import HTTP_206_PARTIAL_CONTENT
from starlette.status import HTTP_300_MULTIPLE_CHOICES
from starlette.status import HTTP_304_NOT_MODIFIED

from .backends.base import MAX_TTL
from .cache_key import CacheKey
from .directives import DirectiveType
from .exceptions import BackendNotFoundError
from .exceptions import CacheXError
from .exceptions import RequestNotFoundError
from .headers import add_vary
from .proxy import BackendProxy
from .proxy import get_backend_or_fallback
from .types import CACHE_KEY_SEPARATOR
from .types import CacheEntry
from .types import CacheKeyBuilder
from .types import HeaderPairs
from .types import escape_key_component
from .types import log_ref

if TYPE_CHECKING:
    from fastapi.routing import APIRoute

# Handler callable accepted by @cache: can return any type (sync or async).
HandlerCallable = Callable[..., Awaitable[object]] | Callable[..., object]

# Wrapper callable produced by @cache: always async and returns Response.
AsyncResponseCallable = Callable[..., Awaitable[Response]]

logger = logging.getLogger(__name__)

_NO_STORE = DirectiveType.NO_STORE.value

# Wall clock behind ``CacheEntry.stored_at`` and the ``Age`` header. Wall time,
# not monotonic, because an entry stored by one process or host is served by
# another. A module attribute so tests can move time without sleeping.
_now = time.time


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


def _append_key_components(key: str, components: Sequence[str]) -> str:
    """Append each component to ``key``, escaped, after another separator."""
    return CACHE_KEY_SEPARATOR.join(
        [key, *(escape_key_component(component) for component in components)]
    )


# RFC 9110 §5.1: a field name is a token.
_FIELD_NAME_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


def _validate_vary(vary: Sequence[str] | None) -> list[str]:
    """Check ``@cache(vary=...)`` and return the names, first spelling of each.

    Raises:
        CacheXError: If ``vary`` is a single string instead of a sequence of
            names, or a name is not a non-empty header field name, or is ``*``.
    """
    if vary is None:
        return []
    if isinstance(vary, (str, bytes)) or not isinstance(vary, Sequence):
        msg = (
            "vary must be a list of header names, e.g. vary=['Accept-Language'], "
            f"got {type(vary).__name__}"
        )
        raise CacheXError(msg)
    names: dict[str, str] = {}
    for name in vary:
        if not isinstance(name, str) or not name or not set(name) <= _FIELD_NAME_CHARS:
            msg = f"vary entries must be header field names, got {name!r}"
            raise CacheXError(msg)
        if name == "*":
            msg = "vary cannot contain '*': the key can only vary on named headers"
            raise CacheXError(msg)
        names.setdefault(name.lower(), name)
    return list(names.values())


# Request headers whose values are credentials: ``vary`` keys on a digest of
# the value instead of the value, so the key (shown by ``get_all_keys()``, the
# monitoring routes and the Redis/Memcached keyspace) never holds a token.
_HASHED_VARY_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        # The session token header (`SessionConfig.header_name`'s default).
        # Inlined, so @cache does not import the deprecated session package.
        "x-session-token",
    }
)
_HASHED_VARY_MARKER = "sha256:"


def _vary_components(request: Request, names: Sequence[str]) -> list[str]:
    """The key components for ``@cache(vary=names)``: ``name=value`` each.

    The name is lower-cased and the value trimmed; repeated header lines are
    joined with ``,`` as RFC 9110 §5.3 allows, and a missing header gives an
    empty value, the same as an empty one. For a credential header
    (``Authorization``, ``Proxy-Authorization``, ``Cookie`` and the session
    subsystem's ``X-Session-Token``) a non-empty value is replaced by
    ``sha256:`` and the full hex SHA-256 of the joined value; an empty or
    missing one stays ``name=``, so anonymous requests share one entry.
    """
    components = []
    for name in names:
        lowered = name.lower()
        value = ",".join(line.strip() for line in request.headers.getlist(name))
        if value and lowered in _HASHED_VARY_HEADERS:
            digest = hashlib.sha256(value.encode("utf-8", "surrogatepass"))
            value = _HASHED_VARY_MARKER + digest.hexdigest()
        components.append(f"{lowered}={value}")
    return components


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


class CacheControl:
    """Manages Cache-Control header directives."""

    def __init__(self) -> None:
        """Initialize an empty CacheControl instance."""
        self.directives: list[str] = []

    def add(self, directive: DirectiveType, value: int | None = None) -> None:
        """Add a Cache-Control directive.

        Args:
            directive: The directive type to add
            value: Optional value for the directive
        """
        if value is not None:
            self.directives.append(f"{directive.value}={value}")
        else:
            self.directives.append(directive.value)

    def __str__(self) -> str:
        """Return the Cache-Control header value as a string."""
        return ", ".join(self.directives)


# Headers that must never be replayed from cache. ``set-cookie`` carries
# per-user state, ``content-type`` is already held by ``CacheEntry.media_type``
# (storing both would emit the header twice), and the rest are either
# connection-scoped or rebuilt for every response.
_UNCACHEABLE_HEADERS = frozenset(
    {
        "set-cookie",
        "content-length",
        "transfer-encoding",
        "connection",
        "date",
        "etag",
        "cache-control",
        "content-type",
        "age",
    }
)


def _is_cacheable_status(status_code: int) -> bool:
    """Whether a response with this status may be stored and replayed.

    Only successful responses are cacheable here. ``206 Partial Content`` is
    excluded because its body is meaningful only for the ``Range`` request that
    produced it, so replaying it to another request would corrupt the response.
    """
    return (
        HTTP_200_OK <= status_code < HTTP_300_MULTIPLE_CHOICES
        and status_code != HTTP_206_PARTIAL_CONTENT
    )


def _cacheable_headers(response: Response) -> HeaderPairs:
    """The handler's own header lines worth storing, in the order sent.

    Every line is kept, so a header sent more than once (several ``Link``
    lines) is replayed line by line rather than as its last value.
    """
    return tuple(
        (name, value)
        for name, value in response.headers.items()
        if name.lower() not in _UNCACHEABLE_HEADERS
    )


def _append_headers(response: Response, headers: Iterable[tuple[str, str]]) -> None:
    """Add every ``(name, value)`` line to ``response``, duplicates included."""
    for name, value in headers:
        response.headers.append(name, value)


# Fields RFC 9110 §15.4.5 asks a 304 to repeat from the 200 it stands in for.
# ``Date`` comes from Starlette, ``ETag`` and ``Cache-Control`` are set on the
# 304 directly, which leaves these three to be carried over.
_REVALIDATION_HEADERS = frozenset({"content-location", "expires", "vary"})


def _age_headers(entry: CacheEntry, ttl: int | None) -> dict[str, str]:
    """The ``Age`` header for a response served from a stored ``entry``.

    ``Cache-Control`` keeps ``max-age=<ttl>`` on a hit: RFC 9111 §4.2.3 has a
    downstream cache compute the remaining freshness as ``max-age`` minus
    ``Age``, so a copy stored here N seconds ago is fresh downstream for
    ``ttl - N`` more seconds, and the total never reaches twice the ttl.

    ``Age`` is a non-negative integer number of seconds (RFC 9111 §5.1). The
    value is clamped to ``[0, ttl]``: ``stored_at`` may come from another
    host's clock, and the backend never keeps an entry longer than ``ttl``, so
    anything outside that range is clock skew. An entry without ``stored_at``
    (written by an older release) gets no ``Age`` at all.
    """
    if entry.stored_at is None:
        return {}
    age = max(0.0, _now() - entry.stored_at)
    if ttl is not None:
        age = min(age, ttl)
    return {"age": str(int(age))}


def _revalidation_headers(headers: Iterable[tuple[str, str]]) -> HeaderPairs:
    """The lines of a response's headers that a 304 must repeat."""
    return tuple(
        (name, value)
        for name, value in headers
        if name.lower() in _REVALIDATION_HEADERS
    )


def _media_type_of(response: Response) -> str | None:
    """The response media type, falling back to a directly-set Content-Type."""
    if response.media_type is not None:
        return response.media_type
    return response.headers.get("content-type")


def _build_cache_control(
    *,
    ttl: int | None,
    stale: Literal["error", "revalidate"] | None,
    stale_ttl: int | None,
    no_cache: bool,
    public: bool,
    private: bool,
    immutable: bool,
    must_revalidate: bool,
) -> str:
    """The ``Cache-Control`` value for a ``@cache`` route's arguments.

    ``no_cache`` sends only ``no-cache`` (plus ``must-revalidate``); otherwise
    the directives follow in a fixed order: scope, ``max-age``,
    ``must-revalidate``, the stale directive, ``immutable``.
    """
    cache_control = CacheControl()
    if no_cache:
        cache_control.add(DirectiveType.NO_CACHE)
        if must_revalidate:
            cache_control.add(DirectiveType.MUST_REVALIDATE)
        return str(cache_control)

    # 1. Access scope (public/private)
    if public:
        cache_control.add(DirectiveType.PUBLIC)
    elif private:
        cache_control.add(DirectiveType.PRIVATE)

    # 2. Cache time settings
    if ttl is not None:
        cache_control.add(DirectiveType.MAX_AGE, ttl)

    # 3. Validation related
    if must_revalidate:
        cache_control.add(DirectiveType.MUST_REVALIDATE)

    # 4. Stale response handling (stale_ttl is validated at decoration time)
    if stale == "revalidate":
        cache_control.add(DirectiveType.STALE_WHILE_REVALIDATE, stale_ttl)
    elif stale == "error":
        cache_control.add(DirectiveType.STALE_IF_ERROR, stale_ttl)

    # 5. Special flags
    if immutable:
        cache_control.add(DirectiveType.IMMUTABLE)

    return str(cache_control)


def _is_request_annotation(annotation: Any) -> bool:
    """Whether an annotation asks for a ``Request`` (or a subclass of one)."""
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return isinstance(annotation, type) and issubclass(annotation, Request)


def _find_request_param(
    func: HandlerCallable, params: list[Parameter]
) -> Parameter | None:
    """The handler's own ``Request`` parameter, if it declares one.

    Annotations are resolved first, so a handler under ``from __future__ import
    annotations`` (where the annotation is the string ``"Request"``),
    ``Annotated[Request, ...]``, or a ``Request`` subclass is recognised
    instead of being given a second, unused request parameter. Resolution can
    fail on a forward reference that does not resolve in the handler's module,
    which must not break decoration: the raw annotations are used instead.
    """
    try:
        hints = get_type_hints(inspect.unwrap(func), include_extras=True)
    except Exception:  # noqa: BLE001 - any resolution failure falls back
        hints = {}

    return next(
        (
            param
            for param in params
            if _is_request_annotation(hints.get(param.name, param.annotation))
        ),
        None,
    )


def _get_response_body(response: Response) -> bytes | None:
    """Return response body bytes, or None for streaming/file responses."""
    return getattr(response, "body", None)


def _etag_for(body: bytes) -> str:
    return f'W/"{hashlib.md5(body).hexdigest()}"'  # noqa: S324


def _weak_etag(etag: str) -> str:
    """An ETag reduced to its opaque tag, so weak and strong forms compare equal."""
    tag = etag.strip()
    if tag[:2].upper() == "W/":
        tag = tag[2:]
    return tag.strip('"')


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` header selects ``etag`` (RFC 9110 §8.8.3.2).

    If-None-Match uses the weak comparison function, so the ``W/`` prefix is
    ignored on both sides, and it may list several validators. ``*`` matches
    whenever the resource exists, which every caller has already established
    before asking.

    Candidates are split on commas. That misreads an opaque-tag containing a
    literal comma, which this library never generates and RFC 9110 treats as
    pathological; the simpler split is worth the edge case.
    """
    if not if_none_match:
        return False

    header = if_none_match.strip()
    if header == "*":
        return True

    target = _weak_etag(etag)
    return any(_weak_etag(candidate) == target for candidate in header.split(","))


def _not_modified(
    etag: str,
    cache_control: str,
    headers: Iterable[tuple[str, str]] = (),
    age: Mapping[str, str] | None = None,
) -> Response:
    """Build the 304 for a successful revalidation.

    ``headers`` is the header lines the 200 for this resource would have
    carried, every line of a repeated field included; RFC 9110
    §15.4.5 requires the fields that steer caching to be repeated on the 304,
    otherwise a cache that stored the 200 would drop them on refresh. ``Date``
    is added by Starlette and the other two are set here. ``age`` is the
    ``Age`` header (see ``_age_headers``) when the 304 is answered from a
    stored entry.
    """
    response = Response(status_code=HTTP_304_NOT_MODIFIED)
    _append_headers(response, _revalidation_headers(headers))
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = cache_control
    response.headers.update(age or {})
    return response


# Response directives by which the handler says its response belongs to one
# caller (``private``) or must not be kept at all (``no-store``).
_UNSHAREABLE_DIRECTIVES = frozenset(
    {DirectiveType.PRIVATE.value, DirectiveType.NO_STORE.value}
)


def _marked_unshareable(response: Response) -> bool:
    """Whether the handler's own ``Cache-Control`` has ``private`` or ``no-store``.

    Directive names are matched as whole tokens, case-insensitively, across
    every ``Cache-Control`` field the response carries.
    """
    return any(
        directive.split("=", 1)[0].strip().lower() in _UNSHAREABLE_DIRECTIVES
        for value in response.headers.getlist("cache-control")
        for directive in value.split(",")
    )


def _unshareable_reason(response: Response) -> str | None:
    """Why a rendered response must not be stored, or None when it may be."""
    if _marked_unshareable(response):
        return "response Cache-Control is private or no-store"
    if "set-cookie" in response.headers:
        return "response sets a cookie"
    return None


def _cache_control_for(
    response: Response, cache_control: str, private_cache_control: str
) -> str:
    """The ``Cache-Control`` to send for a response the handler just rendered.

    A handler that marked its response ``private`` or ``no-store`` keeps its own
    header; the decorator's would widen what the handler allowed. A response
    that sets a cookie gets ``private_cache_control``, so a shared cache in
    front of the app does not store it either.
    """
    if _marked_unshareable(response):
        return ", ".join(response.headers.getlist("cache-control"))
    if "set-cookie" in response.headers:
        return private_cache_control
    return cache_control


def _with_cache_control(
    response: Response, cache_control: str, private_cache_control: str
) -> Response:
    if not _marked_unshareable(response):
        response.headers["Cache-Control"] = _cache_control_for(
            response, cache_control, private_cache_control
        )
    return response


async def _render(
    func: HandlerCallable, request: Request, /, *args: Any, **kwargs: Any
) -> tuple[Response, bytes | None, str | None]:
    """Run the handler; the body and ETag are None for streaming/file responses."""
    response = await get_response(func, request, *args, **kwargs)
    body = _get_response_body(response)
    return response, body, None if body is None else _etag_for(body)


# Attribute a route's response-model TypeAdapter is kept under, so it is built
# once per route rather than on every cache miss.
_ADAPTER_ATTR = "_cachex_response_adapter"


def _serialize_result(route: "APIRoute", result: object) -> object:
    """JSON-compatible content for a handler's non-Response return value.

    With a response model (declared, or inferred from the return annotation)
    the result is validated against it and dumped with the route's
    ``response_model_*`` options, so fields the model leaves out are dropped.
    Otherwise it goes through ``jsonable_encoder``, as FastAPI does.
    """
    if route.response_model is None:
        return jsonable_encoder(result)
    adapter: TypeAdapter[Any] | None = getattr(route, _ADAPTER_ATTR, None)
    if adapter is None:
        adapter = TypeAdapter(route.response_model)
        setattr(route, _ADAPTER_ATTR, adapter)
    validated = adapter.validate_python(result, from_attributes=True)
    return adapter.dump_python(
        validated,
        mode="json",
        include=route.response_model_include,
        exclude=route.response_model_exclude,
        by_alias=route.response_model_by_alias,
        exclude_unset=route.response_model_exclude_unset,
        exclude_defaults=route.response_model_exclude_defaults,
        exclude_none=route.response_model_exclude_none,
    )


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


async def get_response(
    __func: HandlerCallable,
    __request: Request,
    /,
    *args: Any,
    **kwargs: Any,
) -> Response:
    """Get the response from the function.

    Coroutine handlers are awaited. Sync handlers run in the threadpool, as
    FastAPI would run them without the (async) cache wrapper, so blocking I/O
    in a ``def`` handler does not stall the event loop.
    """
    if _is_coroutine_callable(__func):
        result = await cast("Callable[..., Awaitable[object]]", __func)(*args, **kwargs)
    else:
        result = await run_in_threadpool(__func, *args, **kwargs)
    # A sync callable can still hand back an awaitable (a lambda wrapping a
    # coroutine function, say); await it rather than try to encode it.
    if inspect.isawaitable(result):
        result = await result

    # If already a Response object, return it directly
    if isinstance(result, Response):
        return result

    # Get response_class from route if available
    route: APIRoute | None = __request.scope.get("route")
    if route is None:
        msg = "Route not found in request scope"
        raise CacheXError(msg)

    # A placeholder means this route uses the application default. Unwrap the
    # application value instead of calling the route's DefaultPlaceholder.
    route_response_class = route.response_class
    application_default_response_class = __request.app.router.default_response_class
    response_class: type[Response] = cast(
        "type[Response]",
        (
            getattr(
                application_default_response_class,
                "value",
                application_default_response_class,
            )
            if hasattr(route_response_class, "value")
            else route_response_class
        ),
    )

    # Build the response the way FastAPI would have without the cache wrapper:
    # serialize through the response model, apply the route's status code and
    # carry over what the handler set on an injected `response: Response`.
    sub_response = next(
        (value for value in kwargs.values() if isinstance(value, Response)), None
    )
    status_code = route.status_code
    if sub_response is not None and sub_response.status_code:
        status_code = sub_response.status_code
    response_args: dict[str, Any] = {}
    if status_code is not None:
        response_args["status_code"] = status_code

    response = response_class(_serialize_result(route, result), **response_args)
    if not is_body_allowed_for_status_code(response.status_code):
        response.body = b""
    if sub_response is not None:
        response.headers.raw.extend(sub_response.headers.raw)
    return response


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

    Only GET requests go through the cache; other methods run the handler
    unchanged.

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
            header of every response to a GET request, unless it already
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

        # Analyze the original function's signature
        sig: Signature = inspect.signature(func)
        params: list[Parameter] = list(sig.parameters.values())

        # Check if Request is already in the parameters
        found_request: Parameter | None = _find_request_param(func, params)

        # Add Request parameter if it's not present
        if not found_request:
            request_name: str = "__cachex_request"

            request_param = inspect.Parameter(
                request_name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Request,
            )

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
                parameters=[
                    *params[:insert_at],
                    request_param,
                    *params[insert_at:],
                ]
            )

        else:
            request_name = found_request.name

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

        @wraps(func)
        async def serve(*args: Any, **kwargs: Any) -> Response:
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

            # Only cache GET requests
            if req.method != "GET":
                logger.debug(
                    "Non-GET request; bypassing cache for method=%s", req.method
                )
                return await get_response(func, req, *args, **kwargs)

            # Handle special case: no-store (highest priority)
            if no_store:
                response = await get_response(func, req, *args, **kwargs)
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
                response, _, etag = await _render(func, req, *args, **kwargs)
                if not _is_cacheable_status(response.status_code):
                    return response
                if etag is None:
                    # StreamingResponse/FileResponse — cannot compute ETag
                    return _with_cache_control(
                        response, response_cache_control, private_cache_control
                    )
                if _etag_matches(client_etag, etag):
                    logger.debug("304 Not Modified (uncached); path=%s", req.url.path)
                    return _not_modified(
                        etag,
                        _cache_control_for(
                            response, response_cache_control, private_cache_control
                        ),
                        response.headers.items(),
                    )
                response.headers["ETag"] = etag
                logger.debug("Bypassed the backend; path=%s", req.url.path)
                return _with_cache_control(
                    response, response_cache_control, private_cache_control
                )

            # Built only here: the branches above never touch the backend, so a
            # custom key builder would run for nothing.
            cache_key = _append_key_components(
                _build_key(builder, req), _vary_components(req, vary_names)
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
                        func, req, *args, **kwargs
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

                    if _etag_matches(client_etag, current_etag):
                        # For no-cache, compare fresh data with client's ETag
                        logger.debug("304 Not Modified via no-cache; key=%s", cache_key)
                        return _not_modified(
                            current_etag,
                            _cache_control_for(
                                current_response,
                                response_cache_control,
                                private_cache_control,
                            ),
                            current_response.headers.items(),
                        )

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
                    func, req, *args, **kwargs
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
                        CacheEntry(
                            fingerprint=current_etag,
                            content=current_body,
                            media_type=_media_type_of(current_response),
                            status_code=current_response.status_code,
                            headers=_cacheable_headers(current_response),
                            stored_at=_now(),
                        ),
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

            return _with_cache_control(
                current_response, response_cache_control, private_cache_control
            )

        wrapper: AsyncResponseCallable = serve
        if vary_names:

            @wraps(func)
            async def with_vary(*args: Any, **kwargs: Any) -> Response:
                # Read before `serve` pops an injected request parameter.
                req: Request | None = kwargs.get(request_name)
                response = await serve(*args, **kwargs)
                if req is not None and req.method == "GET":
                    # Every GET answer, served from the backend or not: a
                    # shared cache downstream keys on these headers too.
                    add_vary(response.headers, vary_names)
                return response

            wrapper = with_vary

        # Update the wrapper with the new signature
        update_wrapper(wrapper, func)
        wrapper.__signature__ = sig  # type: ignore[attr-defined]

        return wrapper

    return decorator
