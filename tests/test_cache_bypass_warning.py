"""`@cache` warns once when a credential makes it bypass the backend (#326).

The bypass for ``Authorization`` and session requests used to be logged only
at ``DEBUG``, so a route that never hit the cache gave no sign of why. The
first bypass per route and credential kind is now logged at ``WARNING``. The
"once" state lives in each decorated function, so every test's fresh app
starts without it.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.middleware.sessions import (
    SessionMiddleware as StarletteSessionMiddleware,
)

from fastapi_cachex import cache
from fastapi_cachex.cache import _BypassWarner

_LOGGER = "fastapi_cachex.cache"
_BEARER = "Bearer secret-bearer-value-326"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.WARNING
    ]


def _app(**cache_kwargs: object) -> FastAPI:
    """Two cached routes and a route that writes the session."""
    app = FastAPI()
    app.add_middleware(StarletteSessionMiddleware, secret_key="s" * 32)

    @app.get("/products")
    @cache(ttl=60, **cache_kwargs)  # type: ignore[arg-type]
    async def products() -> dict[str, str]:
        return {"products": "all"}

    @app.get("/items/{item_id}")
    @cache(ttl=60, **cache_kwargs)  # type: ignore[arg-type]
    async def item(item_id: int) -> dict[str, int]:
        return {"item": item_id}

    @app.get("/add")
    async def add(request: Request) -> dict[str, bool]:
        request.session["cart"] = ["apple"]
        return {"ok": True}

    return app


def _client(kind: str, **cache_kwargs: object) -> tuple[TestClient, str]:
    """A client whose every request carries credential ``kind``, and its secret."""
    if kind == "authorization":
        app = _app(**cache_kwargs)
        return TestClient(app, headers={"Authorization": _BEARER}), _BEARER
    # Starlette's cookie session with data in it.
    client = TestClient(_app(**cache_kwargs))
    client.get("/add")
    return client, client.cookies["session"]


_KINDS = {
    "authorization": "an Authorization header",
    "session data": "non-empty session data (request.session)",
}


@pytest.mark.parametrize("kind", list(_KINDS))
async def test_first_bypass_warns_once(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    client, secret = _client(kind)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        for _ in range(3):
            response = client.get("/products")
            assert response.headers["Cache-Control"] == "private, max-age=60"

    [warning] = _warnings(caplog)
    assert "route '/products'" in warning
    assert f"the request carried {_KINDS[kind]}," in warning
    assert "public=True" in warning
    assert "cache_authorized=True" in warning
    assert "key_builder" in warning
    # Every request still gets its DEBUG line.
    debug = [r for r in caplog.records if "bypassing the backend" in r.getMessage()]
    assert len(debug) == 3
    # Neither the session cookie nor the header value reaches any log line.
    assert secret not in caplog.text
    assert "secret-bearer-value" not in caplog.text


async def test_each_route_warns(caplog: pytest.LogCaptureFixture) -> None:
    client, _ = _client("authorization")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(2):
            client.get("/products")
            client.get("/items/1")

    warnings = _warnings(caplog)
    assert len(warnings) == 2
    assert "route '/products'" in warnings[0]
    assert "route '/items/{item_id}'" in warnings[1]


async def test_route_is_named_by_its_template(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Distinct paths of one route warn once, and no requested path is logged."""
    client, _ = _client("authorization")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for item_id in range(5):
            client.get(f"/items/{item_id}")

    [warning] = _warnings(caplog)
    assert "route '/items/{item_id}'" in warning
    assert "/items/3" not in warning


async def test_each_credential_kind_warns(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(_app())

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(2):
            client.get("/products", headers={"Authorization": _BEARER})
        client.get("/add")
        for _ in range(2):
            client.get("/products")

    warnings = _warnings(caplog)
    assert len(warnings) == 2
    assert _KINDS["authorization"] in warnings[0]
    assert _KINDS["session data"] in warnings[1]


@pytest.mark.parametrize("opt_in", ["public", "cache_authorized"])
@pytest.mark.parametrize("kind", list(_KINDS))
async def test_opted_in_routes_do_not_warn(
    opt_in: str, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    client, _ = _client(kind, **{opt_in: True})

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(2):
            client.get("/products")

    assert _warnings(caplog) == []


async def test_requests_without_credentials_do_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(_app(), cookies={"theme": "dark"})

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(3):
            client.get("/products")
            client.get("/items/1")

    assert _warnings(caplog) == []


async def test_private_routes_do_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """``private=True`` bypasses on its own; a credential changes nothing there."""
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=60, private=True)
    async def me() -> dict[str, str]:
        return {"me": "x"}

    client = TestClient(app, headers={"Authorization": _BEARER})
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        client.get("/me")

    assert _warnings(caplog) == []


async def test_without_a_route_the_handler_is_named(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Called outside the router, the warning names the handler, not the path."""

    @cache(ttl=60)
    async def handler(request: Request) -> Response:
        return Response("ok")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/client/chosen",
        "query_string": b"",
        "headers": [(b"authorization", _BEARER.encode())],
    }
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(2):
            await handler(request=Request(scope))

    [warning] = _warnings(caplog)
    assert "test_without_a_route_the_handler_is_named.<locals>.handler" in warning
    assert "/client/chosen" not in warning


def test_a_pair_recorded_by_another_thread_is_not_logged_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The check under the lock catches a thread that won between check and add."""
    warner = _BypassWarner("handler")

    class _LosesTheRace(set[tuple[str, str]]):
        """Misses on the unlocked check, as if another thread added it just after."""

        def __contains__(self, item: object) -> bool:
            if not getattr(self, "raced", False):
                self.raced = True
                assert isinstance(item, tuple)
                self.add(item)
                return False
            return super().__contains__(item)

    warner._warned = _LosesTheRace()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "query_string": b"",
        "headers": [],
    }
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        warner(Request(scope), "Authorization header")

    assert _warnings(caplog) == []


async def test_one_handler_on_two_routes_warns_for_each(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The state is per decorated function, and keyed by route inside it."""
    app = FastAPI()

    @app.get("/a")
    @app.get("/b")
    @cache(ttl=60)
    async def shared() -> dict[str, str]:
        return {"ok": "yes"}

    client = TestClient(app, headers={"Authorization": _BEARER})
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for _ in range(2):
            client.get("/a")
            client.get("/b")

    warnings = _warnings(caplog)
    assert len(warnings) == 2
    assert "route '/a'" in warnings[0]
    assert "route '/b'" in warnings[1]
