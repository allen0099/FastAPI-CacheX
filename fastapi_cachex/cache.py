"""Core caching functionality and decorators."""

import hashlib
import inspect
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
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
from starlette.concurrency import run_in_threadpool
from starlette.status import HTTP_200_OK
from starlette.status import HTTP_206_PARTIAL_CONTENT
from starlette.status import HTTP_300_MULTIPLE_CHOICES
from starlette.status import HTTP_304_NOT_MODIFIED

from .directives import DirectiveType
from .exceptions import BackendNotFoundError
from .exceptions import CacheXError
from .exceptions import RequestNotFoundError
from .proxy import BackendProxy
from .proxy import get_backend_or_fallback
from .types import CACHE_KEY_SEPARATOR
from .types import CacheEntry
from .types import CacheKeyBuilder

if TYPE_CHECKING:
    from fastapi.routing import APIRoute

# Handler callable accepted by @cache: can return any type (sync or async).
HandlerCallable = Callable[..., Awaitable[object]] | Callable[..., object]

# Wrapper callable produced by @cache: always async and returns Response.
AsyncResponseCallable = Callable[..., Awaitable[Response]]

logger = logging.getLogger(__name__)

_NO_STORE = DirectiveType.NO_STORE.value


def default_key_builder(request: Request) -> str:
    """Default cache key builder function.

    Generates cache key in format: method|||host|||path|||query_params

    Args:
        request: The FastAPI Request object

    Returns:
        Generated cache key string
    """
    key = (
        f"{request.method}{CACHE_KEY_SEPARATOR}"
        f"{request.headers.get('host', 'unknown')}{CACHE_KEY_SEPARATOR}"
        f"{request.url.path}{CACHE_KEY_SEPARATOR}"
        f"{request.query_params}"
    )
    logger.debug("Built cache key: %s", key)
    return key


async def invalidate(
    request: Request,
    key_builder: CacheKeyBuilder | None = None,
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
            decorator, if any. If None, uses ``default_key_builder``.

    Returns:
        True if a cache entry existed and was deleted, False otherwise.
    """
    builder = key_builder or default_key_builder
    cache_key = builder(request)

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


def _cacheable_headers(response: Response) -> dict[str, str] | None:
    """The handler's own headers worth storing, or None when there are none."""
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _UNCACHEABLE_HEADERS
    }
    return headers or None


# Fields RFC 9110 §15.4.5 asks a 304 to repeat from the 200 it stands in for.
# ``Date`` comes from Starlette, ``ETag`` and ``Cache-Control`` are set on the
# 304 directly, which leaves these three to be carried over.
_REVALIDATION_HEADERS = frozenset({"content-location", "expires", "vary"})


def _revalidation_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """The subset of a response's headers that a 304 must repeat."""
    if not headers:
        return {}
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in _REVALIDATION_HEADERS
    }


def _media_type_of(response: Response) -> str | None:
    """The response media type, falling back to a directly-set Content-Type."""
    if response.media_type is not None:
        return response.media_type
    return response.headers.get("content-type")


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
    etag: str, cache_control: str, headers: Mapping[str, str] | None = None
) -> Response:
    """Build the 304 for a successful revalidation.

    ``headers`` is what the 200 for this resource would have carried; RFC 9110
    §15.4.5 requires the fields that steer caching to be repeated on the 304,
    otherwise a cache that stored the 200 would drop them on refresh. ``Date``
    is added by Starlette and the other two are set here.
    """
    return Response(
        status_code=HTTP_304_NOT_MODIFIED,
        headers={
            **_revalidation_headers(headers),
            "ETag": etag,
            "Cache-Control": cache_control,
        },
    )


def _with_cache_control(response: Response, cache_control: str) -> Response:
    response.headers["Cache-Control"] = cache_control
    return response


async def _render(
    func: HandlerCallable, request: Request, /, *args: Any, **kwargs: Any
) -> tuple[Response, bytes | None, str | None]:
    """Run the handler; the body and ETag are None for streaming/file responses."""
    response = await get_response(func, request, *args, **kwargs)
    body = _get_response_body(response)
    return response, body, None if body is None else _etag_for(body)


def _is_coroutine_callable(func: HandlerCallable) -> bool:
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

    # Convert non-Response result to Response using appropriate response_class
    return response_class(content=result)


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
) -> Callable[[HandlerCallable], AsyncResponseCallable]:
    """Cache decorator for FastAPI route handlers.

    Args:
        ttl: Time-to-live in seconds for cache entries
        stale_ttl: Additional time-to-live for stale cache entries
        stale: Stale response handling strategy ('error' or 'revalidate')
        no_cache: Whether to disable caching
        no_store: Whether to prevent storing responses
        public: Whether responses can be cached by shared caches
        private: Whether responses are for single user only
        immutable: Whether cached responses never change
        must_revalidate: Whether to force revalidation when stale
        key_builder: Custom function to build cache keys. If None, uses default_key_builder

    Returns:
        Decorator function that wraps route handlers with caching logic
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

        def build_cache_control() -> str:
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

        # The header only depends on the decorator arguments, so build it once.
        cache_control = build_cache_control()
        builder = key_builder or default_key_builder

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Response:
            # Resolve backend on every request to support lifespan-configured backends
            cache_backend = get_backend_or_fallback()

            if found_request:
                req: Request | None = kwargs.get(request_name)
            else:
                req = kwargs.pop(request_name, None)

            if not req:
                # Reached when the wrapper is called outside the router, which
                # is the only caller that supplies the request parameter.
                raise RequestNotFoundError

            # Only cache GET requests
            if req.method != "GET":
                logger.debug(
                    "Non-GET request; bypassing cache for method=%s", req.method
                )
                return await get_response(func, req, *args, **kwargs)

            cache_key = builder(req)

            # Handle special case: no-store (highest priority)
            if no_store:
                response = await get_response(func, req, *args, **kwargs)
                logger.debug("no-store active; bypassed cache for key=%s", cache_key)
                return _with_cache_control(response, _NO_STORE)

            client_etag = req.headers.get("if-none-match")

            # A private response belongs to exactly one user, so it must never
            # be read from or written to the shared backend — the default cache
            # key carries no identity, so a stored copy would be served to the
            # next caller. ETag revalidation still works: it compares the
            # client's validator against freshly rendered content.
            if private:
                response, _, etag = await _render(func, req, *args, **kwargs)
                if not _is_cacheable_status(response.status_code):
                    return response
                if etag is None:
                    # StreamingResponse/FileResponse — cannot compute ETag
                    return _with_cache_control(response, cache_control)
                if _etag_matches(client_etag, etag):
                    logger.debug("304 Not Modified (private); key=%s", cache_key)
                    return _not_modified(etag, cache_control, response.headers)
                response.headers["ETag"] = etag
                logger.debug("Private response; bypassed shared cache")
                return _with_cache_control(response, cache_control)

            cached_data = await cache_backend.get(cache_key)

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
                        return _with_cache_control(current_response, cache_control)

                    if _etag_matches(client_etag, current_etag):
                        # For no-cache, compare fresh data with client's ETag
                        logger.debug("304 Not Modified via no-cache; key=%s", cache_key)
                        return _not_modified(
                            current_etag, cache_control, current_response.headers
                        )

                # Compare with cached ETag - if match, return 304
                elif cached_data and _etag_matches(
                    client_etag, cached_data.fingerprint
                ):
                    logger.debug(
                        "304 Not Modified (cached ETag match); key=%s", cache_key
                    )
                    return _not_modified(
                        cached_data.fingerprint, cache_control, cached_data.headers
                    )

            # If we don't have If-None-Match header, check if we have a valid cached copy
            # and can serve it directly (cache hit without ETag comparison)
            if cached_data and not no_cache and ttl is not None:
                logger.debug("Cache HIT (TTL valid); key=%s", cache_key)
                return Response(
                    content=cached_data.content,
                    status_code=cached_data.status_code,
                    media_type=cached_data.media_type,
                    headers={
                        **(cached_data.headers or {}),
                        "ETag": cached_data.fingerprint,
                        "Cache-Control": cache_control,
                    },
                )

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
                    return _with_cache_control(current_response, cache_control)
                logger.debug("Cache MISS; computed fresh ETag for key=%s", cache_key)

            current_response.headers["ETag"] = current_etag

            # Update cache if needed
            if not cached_data or cached_data.fingerprint != current_etag:
                assert (
                    current_body is not None
                )  # guaranteed by early-return guards above
                await cache_backend.set(
                    cache_key,
                    CacheEntry(
                        fingerprint=current_etag,
                        content=current_body,
                        media_type=_media_type_of(current_response),
                        status_code=current_response.status_code,
                        headers=_cacheable_headers(current_response),
                    ),
                    ttl=ttl,
                )
                logger.debug("Updated cache entry; key=%s ttl=%s", cache_key, ttl)

            return _with_cache_control(current_response, cache_control)

        # Update the wrapper with the new signature
        update_wrapper(wrapper, func)
        wrapper.__signature__ = sig  # type: ignore[attr-defined]

        return wrapper

    return decorator
