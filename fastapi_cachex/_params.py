"""The signature of the ``@cache`` wrapper: finding and adding parameters (#562)."""

import inspect
from collections.abc import Callable
from inspect import Parameter
from inspect import Signature
from typing import Annotated
from typing import Any
from typing import NamedTuple
from typing import get_args
from typing import get_origin
from typing import get_type_hints

from fastapi import BackgroundTasks
from fastapi import Request
from fastapi import Response
from fastapi.params import Depends as DependsParam
from starlette.background import BackgroundTasks as StarletteBackgroundTasks

from ._rendering import HandlerCallable


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
