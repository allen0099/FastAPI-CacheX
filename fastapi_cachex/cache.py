"""Core caching functionality and decorators."""

import hashlib
import inspect
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from functools import update_wrapper
from functools import wraps
from inspect import Parameter
from inspect import Signature
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from fastapi import Request
from fastapi import Response
from fastapi.datastructures import DefaultPlaceholder
from starlette.status import HTTP_304_NOT_MODIFIED

from .backends import MemoryBackend
from .directives import DirectiveType
from .exceptions import BackendNotFoundError
from .exceptions import CacheXError
from .exceptions import RequestNotFoundError
from .proxy import BackendProxy
from .types import CACHE_KEY_SEPARATOR
from .types import CacheKeyBuilder
from .types import ETagContent

if TYPE_CHECKING:
    from fastapi.routing import APIRoute

# Handler callable accepted by @cache: can return any type (sync or async).
HandlerCallable = Callable[..., Awaitable[object]] | Callable[..., object]

# Wrapper callable produced by @cache: always async and returns Response.
AsyncResponseCallable = Callable[..., Awaitable[Response]]

logger = logging.getLogger(__name__)


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


async def get_response(
    __func: HandlerCallable,
    __request: Request,
    /,
    *args: Any,
    **kwargs: Any,
) -> Response:
    """Get the response from the function."""
    if inspect.iscoroutinefunction(__func):
        result = await __func(*args, **kwargs)
    else:
        result = __func(*args, **kwargs)

    # If already a Response object, return it directly
    if isinstance(result, Response):
        return result

    # Get response_class from route if available
    route: APIRoute | None = __request.scope.get("route")
    if route is None:  # pragma: no cover
        msg = "Route not found in request scope"
        raise CacheXError(msg)

    if isinstance(route.response_class, DefaultPlaceholder):
        response_class: type[Response] = route.response_class.value

    else:
        response_class = route.response_class

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
        try:
            cache_backend = BackendProxy.get()
        except BackendNotFoundError:
            # Fallback to memory backend if no backend is set
            cache_backend = MemoryBackend()
            BackendProxy.set(cache_backend)
            logger.debug("No backend configured; using MemoryBackend fallback")

        # Analyze the original function's signature
        sig: Signature = inspect.signature(func)
        params: list[Parameter] = list(sig.parameters.values())

        # Check if Request is already in the parameters
        found_request: Parameter | None = next(
            (param for param in params if param.annotation == Request),
            None,
        )

        # Add Request parameter if it's not present
        if not found_request:
            request_name: str = "__cachex_request"

            request_param = inspect.Parameter(
                request_name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Request,
            )

            sig = sig.replace(parameters=[*params, request_param])

        else:
            request_name = found_request.name

        async def get_cache_control(cache_control: CacheControl) -> str:
            # Set Cache-Control headers
            if no_cache:
                cache_control.add(DirectiveType.NO_CACHE)
                if must_revalidate:
                    cache_control.add(DirectiveType.MUST_REVALIDATE)
            else:
                # Handle normal cache control cases
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

                # 4. Stale response handling
                if stale is not None and stale_ttl is None:
                    msg = "stale_ttl must be set if stale is used"
                    raise CacheXError(msg)

                if stale == "revalidate":
                    cache_control.add(DirectiveType.STALE_WHILE_REVALIDATE, stale_ttl)
                elif stale == "error":
                    cache_control.add(DirectiveType.STALE_IF_ERROR, stale_ttl)

                # 5. Special flags
                if immutable:
                    cache_control.add(DirectiveType.IMMUTABLE)

            return str(cache_control)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Response:
            if found_request:
                req: Request | None = kwargs.get(request_name)
            else:
                req = kwargs.pop(request_name, None)

            if not req:  # pragma: no cover
                # Skip coverage for this case, as it should not happen
                raise RequestNotFoundError

            # Only cache GET requests
            if req.method != "GET":
                logger.debug(
                    "Non-GET request; bypassing cache for method=%s", req.method
                )
                return await get_response(func, req, *args, **kwargs)

            # Generate cache key using custom builder or default
            builder = key_builder or default_key_builder
            cache_key = builder(req)
            client_etag = req.headers.get("if-none-match")
            cache_control = await get_cache_control(CacheControl())

            # Handle special case: no-store (highest priority)
            if no_store:
                response = await get_response(func, req, *args, **kwargs)
                cc = CacheControl()
                cc.add(DirectiveType.NO_STORE)
                response.headers["Cache-Control"] = str(cc)
                logger.debug("no-store active; bypassed cache for key=%s", cache_key)
                return response

            # Check cache and handle ETag validation
            cached_data = await cache_backend.get(cache_key)

            current_response = None
            current_etag = None

            if client_etag:
                if no_cache:
                    # Get fresh response first if using no-cache
                    current_response = await get_response(func, req, *args, **kwargs)
                    current_etag = (
                        f'W/"{hashlib.md5(current_response.body).hexdigest()}"'  # noqa: S324
                    )

                    if client_etag == current_etag:
                        # For no-cache, compare fresh data with client's ETag
                        logger.debug("304 Not Modified via no-cache; key=%s", cache_key)
                        return Response(
                            status_code=HTTP_304_NOT_MODIFIED,
                            headers={
                                "ETag": current_etag,
                                "Cache-Control": cache_control,
                            },
                        )

                # Compare with cached ETag - if match, return 304
                elif (
                    cached_data and client_etag == cached_data.etag
                ):  # pragma: no branch
                    # Cache hit with matching ETag: return 304 Not Modified
                    logger.debug(
                        "304 Not Modified (cached ETag match); key=%s", cache_key
                    )
                    return Response(
                        status_code=HTTP_304_NOT_MODIFIED,
                        headers={
                            "ETag": cached_data.etag,
                            "Cache-Control": cache_control,
                        },
                    )

            # If we don't have If-None-Match header, check if we have a valid cached copy
            # and can serve it directly (cache hit without ETag comparison)
            if cached_data and not no_cache and ttl is not None:
                # We have a cached entry and TTL-based caching is enabled
                # Return the cached content directly with 200 OK without revalidation
                logger.debug("Cache HIT (TTL valid); key=%s", cache_key)
                return Response(
                    content=cached_data.content,
                    status_code=200,
                    headers={
                        "ETag": cached_data.etag,
                        "Cache-Control": cache_control,
                    },
                )

            if not current_response or not current_etag:
                # Retrieve the current response if not already done
                current_response = await get_response(func, req, *args, **kwargs)
                current_etag = f'W/"{hashlib.md5(current_response.body).hexdigest()}"'  # noqa: S324
                logger.debug("Cache MISS; computed fresh ETag for key=%s", cache_key)

            # Set ETag header
            current_response.headers["ETag"] = current_etag

            # Update cache if needed
            if not cached_data or cached_data.etag != current_etag:
                # Store in cache if data changed
                await cache_backend.set(
                    cache_key,
                    ETagContent(current_etag, current_response.body),
                    ttl=ttl,
                )
                logger.debug("Updated cache entry; key=%s ttl=%s", cache_key, ttl)

            current_response.headers["Cache-Control"] = cache_control
            return current_response

        # Update the wrapper with the new signature
        update_wrapper(wrapper, func)
        wrapper.__signature__ = sig  # type: ignore[attr-defined]

        return wrapper

    return decorator
