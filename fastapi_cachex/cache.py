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
        has_request = any(
            param.annotation == Request or param.annotation == Optional[Request]
            for param in params
        )
        request_param_name = (
            next(
                (param.name for param in params if param.annotation == Request),
                None,
            )
            if has_request
            else None
        )

        # If the Request parameter does not exist, add it
        if not has_request:
            # Create a new parameter list, keeping the original parameter order
            new_params = []
            request_added = False
            request_param_name = "request"

            for param in params:
                # If *args or **kwargs are encountered, insert request before them
                if (
                    param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD)
                    and not request_added
                ):
                    new_params.append(
                        inspect.Parameter(
                            request_param_name,
                            inspect.Parameter.KEYWORD_ONLY,
                            annotation=Request,
                        )
                    )
                    request_added = True
                new_params.append(param)

            # If request hasn't been added yet (no *args or **kwargs encountered), append it to the end
            if not request_added:
                new_params.append(
                    inspect.Parameter(
                        request_param_name,
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Request,
                    )
                )

            # Create a new signature using the new parameter list
            sig = sig.replace(parameters=new_params)
            # Update the function's __signature__
            func.__signature__ = sig

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Response:  # noqa: C901
            request = (
                kwargs.pop(request_param_name) if request_param_name in kwargs else None
            )
            if_none_match = request.headers.get("if-none-match") if request else None

            # Check if there is an existing response
            existing_response = next(
                (value for value in kwargs.values() if isinstance(value, Response)),
                None,
            )

            # Get the response
            response: Response = existing_response or await func(*args, **kwargs)

            # Generate ETag (hash based on response content)
            if isinstance(response, JSONResponse):
                content = response.body
            else:
                content = (
                    response.body
                    if hasattr(response, "body")
                    else str(response).encode()
                )

            etag = f'W/"{hashlib.md5(content).hexdigest()}"'  # noqa: S324
            response.headers["ETag"] = etag

            # If ETag matches, return 304 Not Modified directly
            if if_none_match and if_none_match == etag:
                return Response(
                    status_code=HTTP_304_NOT_MODIFIED, headers={"ETag": etag}
                )

            # Handle Cache-Control header
            cache_control = CacheControl()

            # Handle special case: no-store (highest priority, should not be combined with other directives)
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
