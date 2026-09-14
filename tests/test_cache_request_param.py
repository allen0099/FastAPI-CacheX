"""How `@cache` finds, or injects, the handler's `Request` parameter."""

import inspect
from typing import Annotated
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import cache

INJECTED = "__cachex_request"


class MyRequest(Request):
    """A `Request` subclass, as an app is free to use."""


def _client(handler: Any, path: str = "/x") -> TestClient:
    app = FastAPI()
    app.get(path)(handler)
    return TestClient(app)


async def _call(handler: Any, path: str = "/x", **kwargs: Any) -> Any:
    """Invoke a decorated handler the way the router would.

    FastAPI cannot bind a `**kwargs` handler as a route at all -- it reads
    `kwargs` as a required query field and answers 422 -- so these handlers are
    driven directly. The decorator still has to produce a wrapper that accepts
    the injected request, keeps it away from the handler, and caches.
    """
    app = FastAPI()
    app.get(path)(handler)
    route = next(route for route in app.routes if getattr(route, "path", "") == path)
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "app": app,
        "route": route,
        "state": {},
    }

    async def receive() -> dict[str, Any]:  # pragma: no cover - never awaited
        return {"type": "http.request", "body": b"", "more_body": False}

    return await handler(**kwargs, **{INJECTED: Request(scope, receive)})


def test_kwargs_handler_can_be_decorated():
    """Decorating a `**kwargs` handler used to raise at import time.

    The injected parameter is keyword-only, and a keyword-only parameter
    cannot follow `**kwargs`, so appending it made `Signature.replace` raise
    while the module was still being imported.
    """

    @cache(ttl=60)
    async def handler(**kwargs: Any) -> dict[str, str]:
        return {"kwargs": ",".join(sorted(kwargs))}

    params = list(inspect.signature(handler).parameters.values())

    assert [param.name for param in params] == [INJECTED, "kwargs"]
    assert params[-1].kind is inspect.Parameter.VAR_KEYWORD


@pytest.mark.asyncio
async def test_kwargs_handler_caches_and_does_not_leak_the_injected_param():
    """The handler runs once and never sees `__cachex_request`."""
    seen: list[list[str]] = []

    @cache(ttl=60)
    async def handler(**kwargs: Any) -> dict[str, bool]:
        seen.append(sorted(kwargs))
        return {"ok": True}

    first = await _call(handler)
    second = await _call(handler)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["Cache-Control"] == "max-age=60"
    # One entry: the second call was served from the cache. And it is empty,
    # so the injected parameter was popped instead of landing in **kwargs.
    assert seen == [[]]


@pytest.mark.asyncio
async def test_kwargs_handler_keeps_its_own_arguments():
    """A real parameter alongside `**kwargs` still reaches the handler."""
    seen: list[tuple[str, list[str]]] = []

    @cache(ttl=60)
    async def handler(name: str, **kwargs: Any) -> dict[str, str]:
        seen.append((name, sorted(kwargs)))
        return {"name": name}

    response = await _call(handler, name="allen")

    assert response.status_code == 200
    assert seen == [("allen", [])]


@pytest.mark.parametrize(
    ("annotation", "label"),
    [
        (Request, "plain"),
        ("Request", "string"),
        (Annotated[Request, "meta"], "annotated"),
        (MyRequest, "subclass"),
    ],
    ids=["plain", "string", "annotated", "subclass"],
)
def test_declared_request_is_not_injected_twice(annotation: Any, label: str):
    """A handler that already asks for a `Request` gets no second one.

    Only the exact `Request` class used to be recognised, so a string
    annotation, an `Annotated[Request, ...]` or a subclass earned the handler
    an extra `__cachex_request` parameter it never used.
    """

    async def handler(request: Any) -> dict[str, str]:
        return {"path": request.url.path, "label": label}

    handler.__annotations__["request"] = annotation
    wrapped = cache(ttl=60)(handler)
    names = list(inspect.signature(wrapped).parameters)

    assert names == ["request"]
    assert INJECTED not in names


def test_string_annotated_request_is_still_usable_at_runtime():
    """Recognising the annotation must also keep the route working."""

    async def handler(request: "Request") -> dict[str, str]:
        return {"path": request.url.path}

    response = _client(cache(ttl=60)(handler)).get("/x")

    assert response.status_code == 200
    assert response.json() == {"path": "/x"}


def test_unresolvable_annotation_falls_back_instead_of_crashing():
    """A forward reference nobody can resolve must not break decoration.

    Resolving annotations means evaluating them against the handler's module
    globals, where a locally scoped name does not exist. Decoration falls back
    to the raw annotations: the handler is treated as not declaring a
    `Request`, which is the same behaviour it had before.
    """

    class Local:
        """Visible to the reader and the type checker, not to `get_type_hints`."""

    async def handler(thing: "Local") -> dict[str, bool]:
        return {"ok": thing is not None}

    wrapped = cache(ttl=60)(handler)

    assert list(inspect.signature(wrapped).parameters) == ["thing", INJECTED]


def test_unresolvable_annotation_still_finds_a_plain_request():
    """The fallback keeps matching the annotations it can read as-is."""

    class Local:
        """Same unresolvable local scope as above."""

    async def handler(request: Request, thing: "Local") -> dict[str, str]:
        return {"path": request.url.path, "thing": str(thing)}

    wrapped = cache(ttl=60)(handler)

    assert list(inspect.signature(wrapped).parameters) == ["request", "thing"]
