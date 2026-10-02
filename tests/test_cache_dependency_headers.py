"""Headers and cookies the dependencies set on the shared `Response` (#233).

FastAPI gives every dependency and the handler one sub-`Response` and merges it
into the result only when the endpoint returns plain data. The `@cache` wrapper
always returns a `Response`, so it merges the dependencies' lines itself, on
every response: with the values of this request, never the ones stored with
an entry. Lines the handler adds are part of its response and are stored.
"""

import inspect
from collections.abc import Iterator
from typing import Annotated
from typing import Any

import pytest
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from fastapi_cachex import cache


def _countdown() -> Iterator[int]:
    return iter(range(10, 0, -1))


def _rate_limit_app(*, declare_response: bool, **cache_kwargs: Any) -> TestClient:
    app = FastAPI()
    left = _countdown()

    def rate_limit(response: Response) -> None:
        response.headers["X-RateLimit-Remaining"] = str(next(left))

    if declare_response:

        @app.get("/item", dependencies=[Depends(rate_limit)])
        @cache(**cache_kwargs)
        async def item_with_response(response: Response) -> dict[str, int]:
            return {"item": 1}

    else:

        @app.get("/item", dependencies=[Depends(rate_limit)])
        @cache(**cache_kwargs)
        async def item() -> dict[str, int]:
            return {"item": 1}

    return TestClient(app)


@pytest.mark.parametrize("declare_response", [False, True], ids=["plain", "declared"])
def test_a_dependency_header_has_this_requests_value_on_a_miss_and_a_hit(
    declare_response: bool,
) -> None:
    client = _rate_limit_app(declare_response=declare_response, ttl=60)

    responses = [client.get("/item") for _ in range(4)]

    # Without the response parameter the header was lost; with it, the miss's
    # value was stored and replayed: 10, 10, 10, 10.
    assert [r.headers.get("x-ratelimit-remaining") for r in responses] == [
        "10",
        "9",
        "8",
        "7",
    ]
    assert all(len(r.headers.get_list("x-ratelimit-remaining")) == 1 for r in responses)
    assert all(r.json() == {"item": 1} for r in responses)
    assert "age" in responses[1].headers  # the later ones are hits


@pytest.mark.parametrize(
    "cache_kwargs",
    [{"ttl": 60}, {"private": True}, {"ttl": 60, "no_cache": True}, {}],
    ids=["hit", "bypass", "no-cache", "bare"],
)
def test_a_304_carries_the_dependency_header(cache_kwargs: dict[str, Any]) -> None:
    client = _rate_limit_app(declare_response=False, **cache_kwargs)

    etag = client.get("/item").headers["ETag"]
    response = client.get("/item", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["x-ratelimit-remaining"] == "9"


def test_a_header_the_handler_sets_is_still_stored_and_replayed() -> None:
    """Handler lines on the sub-response are content: the hit has no handler run (#99)."""
    app = FastAPI()
    left = _countdown()
    calls: list[int] = []

    def rate_limit(response: Response) -> None:
        response.headers["X-RateLimit-Remaining"] = str(next(left))

    @app.get("/tagged", dependencies=[Depends(rate_limit)])
    @cache(ttl=60)
    async def tagged(response: Response) -> dict[str, str]:
        calls.append(1)
        response.headers["X-Tag"] = "blue"
        return {"tag": "blue"}

    client = TestClient(app)
    miss = client.get("/tagged")
    hit = client.get("/tagged")

    assert len(calls) == 1
    for response, remaining in ((miss, "10"), (hit, "9")):
        assert response.headers.get_list("x-tag") == ["blue"]
        assert response.headers.get_list("x-ratelimit-remaining") == [remaining]


def test_a_handler_returning_a_response_gets_the_dependency_header() -> None:
    app = FastAPI()
    left = _countdown()

    def rate_limit(response: Response) -> None:
        response.headers["X-RateLimit-Remaining"] = str(next(left))

    @app.get("/raw", dependencies=[Depends(rate_limit)])
    @cache(ttl=60)
    async def raw() -> JSONResponse:
        return JSONResponse({"raw": True}, headers={"X-Own": "1"})

    client = TestClient(app)
    responses = [client.get("/raw") for _ in range(2)]

    assert [r.headers["x-ratelimit-remaining"] for r in responses] == ["10", "9"]
    assert [r.headers["x-own"] for r in responses] == ["1", "1"]


def _cookie_app(
    sets_cookie: list[bool], **cache_kwargs: Any
) -> tuple[TestClient, list[int]]:
    """A dependency that sets a cookie on the requests ``sets_cookie`` marks."""
    app = FastAPI()
    requests = iter(sets_cookie)
    calls: list[int] = []

    def visit(response: Response) -> None:
        if next(requests):
            response.set_cookie("visit", "1")

    @app.get("/page", dependencies=[Depends(visit)])
    @cache(**cache_kwargs)
    async def page() -> dict[str, bool]:
        calls.append(1)
        return {"page": True}

    return TestClient(app), calls


def test_a_dependency_cookie_is_sent_private_and_not_stored() -> None:
    client, calls = _cookie_app([True, True], ttl=60, public=True)

    responses = [client.get("/page") for _ in range(2)]

    # As for a cookie the handler sets: it may carry per-user state, so
    # neither this backend nor a shared cache downstream keeps the response.
    assert len(calls) == 2
    for response in responses:
        assert response.headers["set-cookie"].startswith("visit=1")
        assert response.headers["cache-control"] == "private, max-age=60"


def test_a_hit_with_a_dependency_cookie_is_private() -> None:
    client, calls = _cookie_app([False, True], ttl=60, public=True)

    stored = client.get("/page")
    hit = client.get("/page")

    assert len(calls) == 1
    assert stored.headers["cache-control"] == "public, max-age=60"
    assert "set-cookie" not in stored.headers
    assert hit.headers["set-cookie"].startswith("visit=1")
    assert hit.headers["cache-control"] == "private, max-age=60"


def test_a_stored_304_with_a_dependency_cookie_is_private() -> None:
    client, _ = _cookie_app([False, True], ttl=60)

    etag = client.get("/page").headers["ETag"]
    response = client.get("/page", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["set-cookie"].startswith("visit=1")
    assert response.headers["cache-control"] == "private, max-age=60"


def test_no_store_keeps_no_store_with_a_dependency_cookie() -> None:
    client, _ = _cookie_app([True], no_store=True)

    response = client.get("/page")

    assert response.headers["set-cookie"].startswith("visit=1")
    assert response.headers["cache-control"] == "no-store"


def test_a_non_get_request_gets_the_dependency_header() -> None:
    app = FastAPI()

    def tag(response: Response) -> None:
        response.headers["X-Dep"] = "1"

    @app.post("/submit", dependencies=[Depends(tag)])
    @cache(ttl=60)
    async def submit() -> JSONResponse:
        return JSONResponse({"ok": True}, headers={"X-Dep": "own"})

    response = TestClient(app).post("/submit")

    # Every line, as FastAPI sends them: only a cacheable GET drops a repeat.
    assert response.headers.get_list("x-dep") == ["own", "1"]
    assert "cache-control" not in response.headers


class _MyResponse(Response):
    """A `Response` subclass, which FastAPI also injects as the sub-response."""


@pytest.mark.parametrize(
    "annotation",
    [Response, "Response", Annotated[Response, "meta"], _MyResponse],
    ids=["plain", "string", "annotated", "subclass"],
)
def test_a_declared_response_is_used_and_not_injected_twice(annotation: Any) -> None:
    async def handler(response: Any) -> dict[str, bool]:
        response.headers["X-Handler"] = "1"
        return {"ok": True}

    handler.__annotations__["response"] = annotation
    app = FastAPI()

    def tag(response: Response) -> None:
        response.headers["X-Dep"] = "1"

    wrapped = cache(ttl=60)(handler)
    app.get("/x", dependencies=[Depends(tag)])(wrapped)

    assert "__cachex_response" not in inspect.signature(wrapped).parameters
    for response in (TestClient(app).get("/x"), TestClient(app).get("/x")):
        assert response.headers.get_list("x-handler") == ["1"]
        assert response.headers.get_list("x-dep") == ["1"]


@pytest.mark.parametrize("overwrite", [True, False], ids=["overwrite", "append"])
def test_the_handlers_value_of_a_dependency_header_wins(overwrite: bool) -> None:
    """On a hit the handler did not run: its stored value replaces the dependency's."""
    app = FastAPI()
    left = _countdown()

    def rate_limit(response: Response) -> None:
        response.headers["X-Rate"] = str(next(left))

    @app.get("/rate", dependencies=[Depends(rate_limit)])
    @cache(ttl=60)
    async def rate(response: Response) -> dict[str, bool]:
        if overwrite:
            response.headers["X-Rate"] = "handler"
        else:
            response.headers.append("X-Rate", "handler")
        return {"ok": True}

    client = TestClient(app)
    miss, hit = client.get("/rate"), client.get("/rate")

    assert miss.headers.get_list("x-rate") == ["handler"]
    assert hit.headers.get_list("x-rate") == ["handler"]


def _private_dependency_app(
    directive: str, **cache_kwargs: Any
) -> tuple[TestClient, list[str]]:
    """A dependency that marks a signed-in user's response with ``directive``."""
    app = FastAPI()
    calls: list[str] = []

    def auth(request: Request, response: Response) -> None:
        if "x-user" in request.headers:
            response.headers["Cache-Control"] = directive

    @app.get("/me", dependencies=[Depends(auth)])
    @cache(**cache_kwargs)
    async def me(request: Request) -> dict[str, str]:
        user = request.headers.get("x-user", "anonymous")
        calls.append(user)
        return {"user": user}

    return TestClient(app), calls


@pytest.mark.parametrize("directive", ["private, no-store", "private", "no-store"])
def test_a_private_header_from_a_dependency_keeps_the_response_out_of_the_backend(
    directive: str,
) -> None:
    client, calls = _private_dependency_app(directive, ttl=60, public=True)

    alice = client.get("/me", headers={"X-User": "alice"})
    anonymous = client.get("/me")

    assert alice.json() == {"user": "alice"}
    # One Cache-Control, the dependency's: the decorator's would widen it.
    assert alice.headers.get_list("cache-control") == [directive]
    # Not stored, so the next caller does not get alice's response.
    assert anonymous.json() == {"user": "anonymous"}
    assert calls == ["alice", "anonymous"]


def test_a_hit_marked_private_by_a_dependency_is_sent_private() -> None:
    client, calls = _private_dependency_app("private", ttl=60, public=True)

    client.get("/me")
    hit = client.get("/me", headers={"X-User": "alice"})

    assert calls == ["anonymous"]
    assert hit.headers.get_list("cache-control") == ["private"]


def test_a_shareable_cache_control_from_a_dependency_is_replaced() -> None:
    client, calls = _private_dependency_app("max-age=5", ttl=60)

    first = client.get("/me", headers={"X-User": "alice"})
    second = client.get("/me", headers={"X-User": "alice"})

    # As a handler's own shareable header: the decorator's replaces it.
    assert first.headers.get_list("cache-control") == ["max-age=60"]
    assert second.headers.get_list("cache-control") == ["max-age=60"]
    assert calls == ["alice"]


def test_bare_cache_keeps_a_shareable_cache_control_from_a_dependency() -> None:
    client, _ = _private_dependency_app("max-age=5")

    response = client.get("/me", headers={"X-User": "alice"})

    # As a handler's own header under a bare `@cache()` (#363).
    assert response.headers.get_list("cache-control") == ["max-age=5"]


def test_a_status_a_dependency_sets_keeps_the_response_out_of_the_backend() -> None:
    app = FastAPI()
    calls: list[int] = []

    def beta(request: Request, response: Response) -> None:
        if "x-beta" in request.headers:
            response.status_code = 203

    @app.get("/page", dependencies=[Depends(beta)])
    @cache(ttl=60)
    async def page() -> dict[str, bool]:
        calls.append(1)
        return {"page": True}

    client = TestClient(app)
    beta_response = client.get("/page", headers={"X-Beta": "1"})
    plain = client.get("/page")

    assert beta_response.status_code == 203
    # Not stored, so the next caller does not get the beta status.
    assert plain.status_code == 200
    assert len(calls) == 2


def test_an_error_response_gets_every_dependency_line() -> None:
    app = FastAPI()

    def tag(response: Response) -> None:
        response.headers["Cache-Control"] = "max-age=5"
        response.headers["X-Dep"] = "1"

    @app.get("/missing", dependencies=[Depends(tag)])
    @cache(ttl=60)
    async def missing() -> JSONResponse:
        return JSONResponse({"error": True}, status_code=404, headers={"X-Dep": "own"})

    response = TestClient(app).get("/missing")

    # As FastAPI sends them: the decorator leaves uncacheable responses alone.
    assert response.status_code == 404
    assert response.headers.get_list("x-dep") == ["own", "1"]
    assert response.headers.get_list("cache-control") == ["max-age=5"]


def _make_response() -> Response:
    return Response(headers={"X-Made": "1"})


async def _made_annotated(
    made: Annotated[Response, Depends(_make_response)],
) -> dict[str, bool]:
    return {"ok": isinstance(made, Response)}


async def _made_default(
    made: Response = Depends(_make_response),
) -> dict[str, bool]:
    return {"ok": isinstance(made, Response)}


@pytest.mark.parametrize(
    "handler", [_made_annotated, _made_default], ids=["annotated", "default"]
)
def test_a_response_returned_by_a_dependency_is_not_the_sub_response(
    handler: Any,
) -> None:
    """A ``Response`` a dependency returns must not stop the injection."""
    app = FastAPI()

    def tag(response: Response) -> None:
        response.headers["X-Dep"] = "1"

    wrapped = cache(ttl=60)(handler)
    app.get("/made", dependencies=[Depends(tag)])(wrapped)

    assert "__cachex_response" in inspect.signature(wrapped).parameters
    response = TestClient(app).get("/made")
    assert response.headers.get_list("x-dep") == ["1"]
    assert "x-made" not in response.headers
