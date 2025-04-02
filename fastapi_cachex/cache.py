import hashlib
import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any
from typing import Literal
from typing import Optional

from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse
from starlette.status import HTTP_304_NOT_MODIFIED

from fastapi_cachex.directives import CacheControlDirective as DirectiveType
from fastapi_cachex.exceptions import CacheXError


class CacheControl:
    def __init__(self) -> None:
        self.directives = []

    def add(self, directive: DirectiveType, value: Optional[int] = None) -> None:
        if value is not None:
            self.directives.append(f"{directive.value}={value}")
        else:
            self.directives.append(directive.value)

    def __str__(self) -> str:
        return ", ".join(self.directives)


def cache(  # noqa: C901
    ttl: Optional[int] = None,
    stale_ttl: Optional[int] = None,
    stale: Literal["error", "revalidate"] | None = None,
    no_cache: bool = False,
    no_store: bool = False,
    public: bool = False,
    private: bool = False,
    immutable: bool = False,
    must_revalidate: bool = False,
) -> Callable:
    def decorator(func: Callable) -> Callable:  # noqa: C901
        # Analyze the original function's signature
        sig = inspect.signature(func)
        params = list(sig.parameters.values())

        # Check if Request is already in the parameters
        has_request = any(
            param.annotation == Request or param.annotation == Optional[Request]
            for param in params
        )

        # Add Request parameter if it's not present
        if not has_request:
            new_params = []

            request_param = inspect.Parameter(
                "request",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Request,
            )
            new_params.append(request_param)

            sig = sig.replace(parameters=[*params, *new_params])
            func.__signature__ = sig

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Response:  # noqa: C901
            # Get request from kwargs
            request = kwargs.pop("request", None) if "request" in kwargs else None

            # Get if-none-match header
            if_none_match = request.headers.get("if-none-match") if request else None

            # Get the response
            if inspect.iscoroutinefunction(func):
                response = await func(*args, **kwargs)
            else:
                response = func(*args, **kwargs)

            # Generate ETag (hash based on response content)
            if isinstance(response, JSONResponse):
                content = response.body
            else:
                content = (
                    response.body
                    if hasattr(response, "body")
                    else str(response).encode()
                )

            # Calculate ETag
            etag = f'W/"{hashlib.md5(content).hexdigest()}"'  # noqa: S324

            # If ETag matches, return 304 Not Modified
            if if_none_match == etag:
                return Response(
                    status_code=HTTP_304_NOT_MODIFIED,
                    headers={"ETag": etag, "Cache-Control": ""},
                )

            # Add ETag to response headers
            response.headers["ETag"] = etag

            # Handle Cache-Control header
            cache_control = CacheControl()

            # Handle special case: no-store (highest priority)
            if no_store:
                cache_control.add(DirectiveType.NO_STORE)
                response.headers["Cache-Control"] = str(cache_control)
                return response

            # Handle special case: no-cache
            if no_cache:
                cache_control.add(DirectiveType.NO_CACHE)
                if must_revalidate:
                    cache_control.add(DirectiveType.MUST_REVALIDATE)
                response.headers["Cache-Control"] = str(cache_control)
                return response

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
                raise CacheXError("stale_ttl must be set if stale is used")

            if stale == "revalidate":
                cache_control.add(DirectiveType.STALE_WHILE_REVALIDATE, stale_ttl)
            elif stale == "error":
                cache_control.add(DirectiveType.STALE_IF_ERROR, stale_ttl)

            # 5. Special flags
            if immutable:
                cache_control.add(DirectiveType.IMMUTABLE)

            response.headers["Cache-Control"] = str(cache_control)
            return response

        return wrapper

    return decorator
