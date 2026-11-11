"""``@cache(serve_stale=True)``: the server's own cache acts on ``stale`` (#539).

Time is moved by patching ``fastapi_cachex._stored_response._now``; the memory
backend keeps real time, so an entry stays in it while the patched clock runs
ahead. Starlette's ``TestClient`` returns once the request's background tasks
have run, so a refresh has finished by the time ``client.get`` returns.
"""

import asyncio
import logging
import time
from collections.abc import Iterator
from typing import Annotated
from typing import Any

import pytest
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask
from starlette.background import BackgroundTasks as StarletteBackgroundTasks

from fastapi_cachex import _stored_response as stored_response
from fastapi_cachex._coalesce import _REFRESHING
from fastapi_cachex._coalesce import _claim_refresh
from fastapi_cachex._coalesce import _release_refresh
from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.proxy import BackendProxy

try:  # Starlette's TestClient moved to httpx2; the `lowest` env still has httpx.
    import httpx2 as httpx  # type: ignore[import-not-found, unused-ignore]
except ImportError:  # pragma: no cover - depends on the installed starlette
    import httpx  # type: ignore[no-redef, import-not-found, unused-ignore]

TTL = 60
STALE_TTL = 30
KEY = "http:v2|GET|testserver|/item|"


class _Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(stored_response, "_now", clock)
    return clock


class _Handler:
    """Counts runs; ``fail`` makes the next runs raise or answer an error."""

    def __init__(self) -> None:
        self.runs = 0
        self.fail: Exception | int | None = None
        self.same_body = False


def _app(stale: str, **cache_kwargs: Any) -> tuple[TestClient, _Handler]:
    handler = _Handler()
    app = FastAPI()
    options: dict[str, Any] = {
        "ttl": TTL,
        "stale": stale,
        "stale_ttl": STALE_TTL,
        "serve_stale": True,
        "debug_header": True,
        **cache_kwargs,
    }

    @app.api_route("/item", methods=["GET", "HEAD"])
    @cache(**options)
    async def item() -> Response:
        handler.runs += 1
        if isinstance(handler.fail, Exception):
            raise handler.fail
        if isinstance(handler.fail, int):
            return Response(content=b"down", status_code=handler.fail)
        body = "same" if handler.same_body else f"run {handler.runs}"
        return Response(content=body, media_type="text/plain")

    return TestClient(app, raise_server_exceptions=False), handler


def _seen(response: Any) -> tuple[int, str, str]:
    return response.status_code, response.text, response.headers.get("x-cache", "")


def _seconds_left() -> float:
    """How much longer the memory backend keeps the route's entry."""
    expiry = BackendProxy.get().cache[KEY].expiry  # type: ignore[attr-defined]
    assert expiry is not None
    return float(expiry - time.time())


# --- stale="revalidate": answer from the old entry, refresh afterwards -------


def test_revalidate_serves_the_old_entry_and_refreshes_after_the_response(
    clock: _Clock,
) -> None:
    client, handler = _app("revalidate")
    assert _seen(client.get("/item")) == (200, "run 1", "MISS")
    clock.now += TTL - 1
    assert _seen(client.get("/item")) == (200, "run 1", "HIT")

    clock.now += 2  # past the ttl, inside the stale window
    stale = client.get("/item")

    # Answered with what was stored, although the handler has run since.
    assert _seen(stale) == (200, "run 1", "STALE")
    assert stale.headers["age"] == str(TTL + 1)
    assert handler.runs == 2
    fresh = client.get("/item")
    assert _seen(fresh) == (200, "run 2", "HIT")
    assert fresh.headers["age"] == "0"
    assert set() == _REFRESHING


def test_the_entry_is_kept_for_the_ttl_plus_the_stale_window(clock: _Clock) -> None:
    client, _ = _app("revalidate")
    client.get("/item")

    assert TTL + STALE_TTL - 5 < _seconds_left() <= TTL + STALE_TTL


def test_past_the_stale_window_the_entry_is_not_served(clock: _Clock) -> None:
    client, handler = _app("revalidate")
    client.get("/item")

    clock.now += TTL + STALE_TTL

    # Rendered for this request, as on any miss.
    assert _seen(client.get("/item")) == (200, "run 2", "MISS")
    assert handler.runs == 2


def test_an_unchanged_response_is_stored_again_so_it_is_fresh(clock: _Clock) -> None:
    client, handler = _app("revalidate")
    handler.same_body = True
    client.get("/item")
    clock.now += TTL + 1

    assert _seen(client.get("/item")) == (200, "same", "STALE")

    again = client.get("/item")
    assert _seen(again) == (200, "same", "HIT")
    assert again.headers["age"] == "0"
    assert handler.runs == 2


def test_a_matching_etag_on_a_stale_entry_gets_304_and_a_refresh(
    clock: _Clock,
) -> None:
    client, handler = _app("revalidate")
    etag = client.get("/item").headers["etag"]
    clock.now += TTL + 1

    response = client.get("/item", headers={"If-None-Match": etag})

    assert (response.status_code, response.headers["x-cache"]) == (304, "STALE")
    assert handler.runs == 2


def test_head_is_answered_stale_without_starting_a_refresh(clock: _Clock) -> None:
    client, handler = _app("revalidate")
    client.get("/item")
    clock.now += TTL + 1

    response = client.head("/item")

    assert (response.status_code, response.headers["x-cache"]) == (200, "STALE")
    assert handler.runs == 1


def test_only_one_refresh_per_key_at_a_time(clock: _Clock) -> None:
    client, handler = _app("revalidate")
    client.get("/item")
    clock.now += TTL + 1
    # Another request of this process is refreshing the key.
    assert _claim_refresh(BackendProxy.get(), KEY)

    assert _seen(client.get("/item")) == (200, "run 1", "STALE")

    assert handler.runs == 1


def test_a_refresh_claim_is_per_backend_and_key() -> None:
    backend = object()

    assert _claim_refresh(backend, "k")
    assert not _claim_refresh(backend, "k")
    assert _claim_refresh(backend, "other")
    assert _claim_refresh(object(), "k")
    _release_refresh(backend, "k")
    assert _claim_refresh(backend, "k")


async def test_requests_served_stale_together_run_the_handler_once(
    clock: _Clock,
) -> None:
    """Each adds a refresh; the later ones find the key claimed or fresh."""
    client, handler = _app("revalidate")
    client.get("/item")
    clock.now += TTL + 1

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="http://testserver"
    ) as concurrent:
        responses = await asyncio.gather(*(concurrent.get("/item") for _ in range(8)))

    # The old entry, or for a request that came after the refresh, the new one.
    assert {_seen(response) for response in responses} <= {
        (200, "run 1", "STALE"),
        (200, "run 2", "HIT"),
    }
    assert _seen(responses[0]) == (200, "run 1", "STALE")
    assert handler.runs == 2
    assert set() == _REFRESHING


def test_a_refresh_that_cannot_be_stored_says_so(
    clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    app = FastAPI()
    cookie: list[int] = []

    @app.get("/item")
    @cache(ttl=TTL, stale="revalidate", stale_ttl=STALE_TTL, serve_stale=True)
    async def item(response: Response) -> dict[str, bool]:
        if cookie:
            response.set_cookie("seen", "1")
        return {"ok": True}

    client = TestClient(app)
    client.get("/item")
    clock.now += TTL + 1
    cookie.append(1)

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.cache"):
        stale = client.get("/item")

    assert "set-cookie" not in stale.headers
    assert "Refresh not stored" in caplog.text


def test_a_background_task_on_the_refreshed_response_runs(clock: _Clock) -> None:
    ran: list[str] = []
    app = FastAPI()

    @app.get("/item")
    @cache(ttl=TTL, stale="revalidate", stale_ttl=STALE_TTL, serve_stale=True)
    async def item() -> Response:
        return Response(b"ok", background=BackgroundTask(ran.append, "handler task"))

    client = TestClient(app)
    client.get("/item")
    clock.now += TTL + 1
    ran.clear()

    client.get("/item")

    assert ran == ["handler task"]


def test_a_handler_annotated_with_starlettes_background_tasks(clock: _Clock) -> None:
    """FastAPI fills one such parameter: the decorator must not add another."""
    app = FastAPI()

    @app.get("/item")
    @cache(ttl=TTL, stale="revalidate", stale_ttl=STALE_TTL, serve_stale=True)
    async def item(tasks: StarletteBackgroundTasks) -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/item").status_code == 200
    clock.now += TTL + 1
    assert client.get("/item").status_code == 200


@pytest.mark.parametrize("failure", [RuntimeError("boom"), 503])
def test_a_failed_refresh_is_logged_and_the_stale_entry_stays(
    clock: _Clock, caplog: pytest.LogCaptureFixture, failure: Exception | int
) -> None:
    client, handler = _app("revalidate")
    client.get("/item")
    clock.now += TTL + 1
    handler.fail = failure

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.cache"):
        assert _seen(client.get("/item")) == (200, "run 1", "STALE")

    assert "stale entry" in caplog.text
    assert set() == _REFRESHING
    # Still there, and the next request tries again.
    handler.fail = None
    assert _seen(client.get("/item")) == (200, "run 1", "STALE")
    assert _seen(client.get("/item")) == (200, "run 3", "HIT")


def test_the_refresh_runs_while_the_request_dependencies_are_open(
    clock: _Clock,
) -> None:
    """And the request's other background tasks still run."""
    events: list[str] = []

    def session(tasks: BackgroundTasks) -> Iterator[dict[str, bool]]:
        state = {"open": True}
        tasks.add_task(events.append, "dependency task")
        yield state
        state["open"] = False

    app = FastAPI()

    @app.get("/item")
    @cache(ttl=TTL, stale="revalidate", stale_ttl=STALE_TTL, serve_stale=True)
    async def item(
        tasks: BackgroundTasks, db: Annotated[dict[str, bool], Depends(session)]
    ) -> dict[str, bool]:
        events.append(f"handler, session open: {db['open']}")
        tasks.add_task(events.append, "handler task")
        return {"ok": True}

    client = TestClient(app)
    client.get("/item")
    clock.now += TTL + 1
    events.clear()

    client.get("/item")

    assert events == [
        "dependency task",
        "handler, session open: True",
        "handler task",
    ]
    # The parameter the decorator adds for this is not part of the API.
    assert "parameters" not in app.openapi()["paths"]["/item"]["get"]


# --- stale="error": the old entry answers for a failing handler --------------


def test_error_mode_runs_the_handler_and_stores_when_it_succeeds(
    clock: _Clock,
) -> None:
    client, handler = _app("error")
    client.get("/item")
    clock.now += TTL + 1

    assert _seen(client.get("/item")) == (200, "run 2", "MISS")
    assert _seen(client.get("/item")) == (200, "run 2", "HIT")
    assert handler.runs == 2


def test_error_mode_stores_an_unchanged_response_again(clock: _Clock) -> None:
    """Skipping the write for an equal ETag would leave the entry stale."""
    client, handler = _app("error")
    handler.same_body = True
    client.get("/item")
    clock.now += TTL + 1

    assert _seen(client.get("/item")) == (200, "same", "MISS")

    again = client.get("/item")
    assert _seen(again) == (200, "same", "HIT")
    assert again.headers["age"] == "0"


@pytest.mark.parametrize(
    "failure", [RuntimeError("boom"), HTTPException(status_code=502), 500, 503]
)
def test_error_mode_answers_a_failure_from_the_stale_entry(
    clock: _Clock, caplog: pytest.LogCaptureFixture, failure: Exception | int
) -> None:
    client, handler = _app("error")
    client.get("/item")
    clock.now += TTL + 1
    handler.fail = failure

    with caplog.at_level(logging.WARNING, logger="fastapi_cachex.cache"):
        response = client.get("/item")

    assert _seen(response) == (200, "run 1", "STALE")
    assert response.headers["age"] == str(TTL + 1)
    assert "serving the stale entry" in caplog.text
    # The entry is as it was: once the handler recovers, it is replaced.
    handler.fail = None
    assert _seen(client.get("/item")) == (200, "run 3", "MISS")


class _DomainNotFoundError(Exception):
    """An exception the application maps to a 404 of its own."""


def test_error_mode_lets_an_exception_the_app_handles_through(clock: _Clock) -> None:
    client, handler = _app("error")

    async def not_found(request: Request, error: Exception) -> Response:
        return Response(b"no such item", status_code=404)

    app: FastAPI = client.app  # type: ignore[assignment]
    app.add_exception_handler(_DomainNotFoundError, not_found)

    client.get("/item")
    clock.now += TTL + 1
    handler.fail = _DomainNotFoundError()

    assert _seen(client.get("/item"))[:2] == (404, "no such item")


def test_error_mode_lets_a_client_error_through(clock: _Clock) -> None:
    """A 404 is the handler's answer, not a failure."""
    client, handler = _app("error")
    client.get("/item")
    clock.now += TTL + 1
    handler.fail = HTTPException(status_code=404)

    assert client.get("/item").status_code == 404


@pytest.mark.parametrize("elapsed", [0, TTL + STALE_TTL])
def test_error_mode_has_nothing_to_fall_back_on_without_a_stale_entry(
    clock: _Clock, elapsed: int
) -> None:
    """A fresh entry is a hit; a cold key or one past the window is an error."""
    client, handler = _app("error")
    if elapsed:
        client.get("/item")
        clock.now += elapsed
    handler.fail = 503

    assert client.get("/item").status_code == 503


# --- arguments ----------------------------------------------------------------


def test_without_serve_stale_nothing_changes(clock: _Clock) -> None:
    client, _ = _app("revalidate", serve_stale=False)
    client.get("/item")

    assert _seconds_left() <= TTL
    clock.now += TTL + 1
    # The memory backend runs on real time, so the entry is still there; it
    # is served as the hit it would be on any backend before its ttl.
    assert _seen(client.get("/item")) == (200, "run 1", "HIT")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"must_revalidate": True}, "cannot be combined with"),
        ({"no_cache": True}, "cannot be combined with"),
        ({"private": True}, "cannot be combined with"),
        ({"ttl": None}, "needs a positive ttl"),
        ({"ttl": 0}, "needs a positive ttl"),
        ({"serve_stale": "yes"}, "serve_stale must be a bool"),
    ],
)
def test_arguments_that_could_never_serve_stale_are_rejected(
    kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(CacheXError, match=message):
        _app("revalidate", **kwargs)


def test_no_store_route_warns_that_it_is_ignored() -> None:
    with pytest.warns(UserWarning, match="serve_stale"):
        _app("revalidate", no_store=True)


def test_serve_stale_needs_stale() -> None:
    with pytest.raises(CacheXError, match="serve_stale needs stale"):
        cache(ttl=TTL, serve_stale=True)(lambda: None)
