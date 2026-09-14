"""The `@cache` wrapper's own error paths.

Both of these were marked `pragma: no cover` with a note that they could not
happen. They can: the wrapper is a plain async callable, and anything that
calls it without going through the router reaches them.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse

from fastapi_cachex import cache
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.exceptions import RequestNotFoundError


def _scope(app: FastAPI, route: Any, path: str = "/x") -> dict[str, Any]:
    return {
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


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.asyncio
async def test_missing_request_raises_request_not_found():
    """Calling the wrapper without the injected request is an error."""

    @cache(ttl=60)
    async def handler() -> dict[str, bool]:
        return {"ok": True}

    with pytest.raises(RequestNotFoundError):
        await handler()


@pytest.mark.asyncio
async def test_declared_request_left_unbound_raises_request_not_found():
    """The `found_request` branch reads the parameter instead of popping it."""

    @cache(ttl=60)
    async def handler(request: Request) -> dict[str, bool]:
        return {"ok": True}

    with pytest.raises(RequestNotFoundError):
        await handler(request=None)


@pytest.mark.asyncio
async def test_missing_route_in_scope_raises_cachex_error():
    """Building a response needs the route's `response_class`."""
    app = FastAPI()

    @cache(ttl=60)
    async def handler() -> dict[str, bool]:
        return {"ok": True}

    app.get("/x")(handler)
    scope = _scope(app, route=None)
    del scope["route"]

    with pytest.raises(CacheXError, match="Route not found in request scope"):
        await handler(__cachex_request=Request(scope, _receive))


@pytest.mark.asyncio
async def test_response_returning_handler_does_not_need_the_route():
    """A handler that already returns a `Response` never looks the route up."""
    app = FastAPI()

    @cache(ttl=60)
    async def handler() -> JSONResponse:
        return JSONResponse({"ok": True})

    app.get("/x")(handler)
    scope = _scope(app, route=None)
    del scope["route"]

    response = await handler(__cachex_request=Request(scope, _receive))

    assert response.status_code == 200
