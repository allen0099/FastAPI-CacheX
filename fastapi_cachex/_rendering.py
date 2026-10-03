"""Running the handler and building its response, with its dependencies' headers."""

import inspect
from collections import Counter
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Sequence
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from fastapi import Request
from fastapi import Response
from fastapi.encoders import jsonable_encoder
from fastapi.utils import is_body_allowed_for_status_code
from pydantic import TypeAdapter
from starlette.concurrency import run_in_threadpool
from starlette.status import HTTP_304_NOT_MODIFIED

from ._cache_control import _has_unshareable_directive
from ._cache_control import _marked_unshareable
from ._callables import _is_coroutine_callable
from ._stored_response import _etag_for
from ._stored_response import _get_response_body
from ._stored_response import _is_cacheable_status
from .exceptions import CacheXError

if TYPE_CHECKING:
    from fastapi.routing import APIRoute

# Handler callable accepted by @cache: can return any type (sync or async).
HandlerCallable = Callable[..., Awaitable[object]] | Callable[..., object]

# Wrapper callable produced by @cache: always async and returns Response.
AsyncResponseCallable = Callable[..., Awaitable[Response]]


def _split_header_lines(
    lines: Iterable[tuple[bytes, bytes]], before: Iterable[tuple[bytes, bytes]]
) -> tuple[list[tuple[bytes, bytes]], list[tuple[bytes, bytes]]]:
    """Split a sub-response's header lines into those of ``before`` and the rest.

    FastAPI resolves the dependencies before it calls the handler, so the
    sub-response's lines when the wrapper starts (``before``) are the
    dependencies' and the lines added since are the handler's. Lines are
    matched as a multiset, so a repeated line is counted once per copy.
    """
    remaining = Counter(before)
    kept: list[tuple[bytes, bytes]] = []
    added: list[tuple[bytes, bytes]] = []
    for line in lines:
        if remaining[line] > 0:
            remaining[line] -= 1
            kept.append(line)
        else:
            added.append(line)
    return kept, added


def _sets_cookie(lines: Iterable[tuple[bytes, bytes]]) -> bool:
    return any(name.lower() == b"set-cookie" for name, _ in lines)


def _cache_control_values(lines: Iterable[tuple[bytes, bytes]]) -> list[str]:
    return [
        value.decode("latin-1")
        for name, value in lines
        if name.lower() == b"cache-control"
    ]


def _dependency_unshareable_reason(
    dependency_lines: Iterable[tuple[bytes, bytes]],
) -> str | None:
    """Why the dependencies' lines keep a response out of the backend, if they do."""
    lines = list(dependency_lines)
    if _has_unshareable_directive(_cache_control_values(lines)):
        return "a dependency's Cache-Control is private or no-store"
    if _sets_cookie(lines):
        return "a dependency sets a cookie"
    return None


def _with_dependency_headers(
    response: Response,
    sub_response: Response | None,
    dependency_lines: Sequence[tuple[bytes, bytes]],
    private_cache_control: str,
    *,
    cacheable_get: bool,
) -> Response:
    """Add the header lines the dependencies set on this request (#233).

    FastAPI merges the sub-response into the response only when the handler
    returns plain data, and the wrapper always returns a ``Response``, so it
    merges them itself: on a miss, a hit and a 304 alike, with the values of
    this request rather than those stored with the entry.

    On a cacheable GET response, ``Cache-Control`` is decided as for the
    handler's own lines: a handler's ``private`` or ``no-store`` header (or
    ``no_store=True``) is kept; otherwise a dependency's ``private`` or
    ``no-store`` header replaces the decorator's, and a ``Set-Cookie`` makes it
    ``private``. Any header the response already carries (other than
    ``Set-Cookie``) is not added, the decorator's ``Cache-Control`` included:
    the handler set it over the dependency's, which on a hit is the stored
    value.
    Any other response gets every line, as FastAPI would send them.
    """
    if sub_response is None or not dependency_lines:
        return response
    current, _ = _split_header_lines(sub_response.headers.raw, dependency_lines)
    if not cacheable_get or not (
        response.status_code == HTTP_304_NOT_MODIFIED
        or _is_cacheable_status(response.status_code)
    ):
        response.headers.raw.extend(current)
        return response
    own = {name.lower() for name, _ in response.headers.raw}
    response.headers.raw.extend(
        (name, value)
        for name, value in current
        if name.lower() == b"set-cookie" or name.lower() not in own
    )
    if _marked_unshareable(response):
        return response
    dependency_cache_control = _cache_control_values(current)
    if _has_unshareable_directive(dependency_cache_control):
        response.headers["Cache-Control"] = ", ".join(dependency_cache_control)
    elif _sets_cookie(current):
        response.headers["Cache-Control"] = private_cache_control
    return response


async def _render(
    func: HandlerCallable,
    request: Request,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    sub_response: Response | None,
    dependency_lines: Sequence[tuple[bytes, bytes]],
) -> tuple[Response, bytes | None, str | None]:
    """Run the handler; the body and ETag are None for streaming/file responses."""
    response = await _respond(
        func,
        request,
        args,
        kwargs,
        sub_response=sub_response,
        dependency_lines=dependency_lines,
    )
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
    in a ``def`` handler does not stall the event loop. Every header line of a
    ``Response`` among ``kwargs`` is carried over onto a plain-data result.
    """
    sub_response = next(
        (value for value in kwargs.values() if isinstance(value, Response)), None
    )
    return await _respond(
        __func, __request, args, kwargs, sub_response=sub_response, dependency_lines=()
    )


async def _respond(
    func: HandlerCallable,
    request: Request,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    sub_response: Response | None,
    dependency_lines: Sequence[tuple[bytes, bytes]],
) -> Response:
    """Run the handler and build its response, as ``get_response`` describes.

    Only the lines added to ``sub_response`` beyond ``dependency_lines`` are
    carried over here: the dependencies' own lines are added to every
    response the wrapper sends (``_with_dependency_headers``), and must not be
    stored with the entry.
    """
    if _is_coroutine_callable(func):
        result = await cast("Callable[..., Awaitable[object]]", func)(*args, **kwargs)
    else:
        result = await run_in_threadpool(func, *args, **kwargs)
    # A sync callable can still hand back an awaitable (a lambda wrapping a
    # coroutine function, say); await it rather than try to encode it.
    if inspect.isawaitable(result):
        result = await result

    # If already a Response object, return it directly
    if isinstance(result, Response):
        return result

    # Get response_class from route if available
    route: APIRoute | None = request.scope.get("route")
    if route is None:
        msg = "Route not found in request scope"
        raise CacheXError(msg)

    # A placeholder means this route uses the application default. Unwrap the
    # application value instead of calling the route's DefaultPlaceholder.
    route_response_class = route.response_class
    application_default_response_class = request.app.router.default_response_class
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
    # carry over what the handler set on the injected `response: Response`.
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
        _, added = _split_header_lines(sub_response.headers.raw, dependency_lines)
        response.headers.raw.extend(added)
    return response
