"""``@cache`` answers HEAD from the entry a GET stored (#253)."""

from typing import Any

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex import build_cache_key
from fastapi_cachex.cache import cache
from fastapi_cachex.proxy import BackendProxy

GET_KEY = "http:v2|GET|testserver|/items|"


def _app(**cache_kwargs: Any) -> tuple[TestClient, list[str]]:
    app = FastAPI()
    calls: list[str] = []

    @app.api_route("/items", methods=["GET", "HEAD"])
    @cache(ttl=60, **cache_kwargs)
    async def items(request: Request) -> Response:
        calls.append(request.method)
        # A handler may skip the body for HEAD; only a GET's may be stored.
        body = b"" if request.method == "HEAD" else b'{"items": [1, 2, 3]}'
        return Response(content=body, media_type="application/json")

    return TestClient(app), calls


async def test_head_is_served_from_the_get_entry() -> None:
    client, calls = _app()
    get = client.get("/items")

    head = client.head("/items")

    assert calls == ["GET"]
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(get.content))
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["cache-control"] == "max-age=60"
    assert "age" in head.headers
    assert await BackendProxy.get().get_all_keys() == [GET_KEY]


def test_head_revalidates_against_the_get_entry() -> None:
    client, calls = _app()
    etag = client.get("/items").headers["etag"]

    head = client.head("/items", headers={"If-None-Match": etag})

    assert calls == ["GET"]
    assert head.status_code == 304
    assert head.headers["etag"] == etag


async def test_head_miss_runs_the_handler_and_stores_nothing() -> None:
    client, calls = _app()

    head = client.head("/items")
    get = client.get("/items")

    assert calls == ["HEAD", "GET"]
    assert head.headers["cache-control"] == "max-age=60"
    assert "etag" in head.headers
    assert get.content == b'{"items": [1, 2, 3]}'
    assert await BackendProxy.get().get_all_keys() == [GET_KEY]


def test_a_custom_key_builder_builds_the_get_key_for_head() -> None:
    methods: list[str] = []

    def per_tenant(request: Request) -> str:
        methods.append(request.method)
        return build_cache_key(request, request.headers.get("x-tenant", ""))

    client, calls = _app(key_builder=per_tenant)
    client.get("/items", headers={"X-Tenant": "a"})

    head = client.head("/items", headers={"X-Tenant": "a"})

    assert calls == ["GET"]
    assert head.status_code == 200
    assert methods == ["GET", "GET"]


def test_head_keys_on_and_sends_vary() -> None:
    client, calls = _app(vary=["Accept-Language"])
    client.get("/items", headers={"Accept-Language": "de"})

    de = client.head("/items", headers={"Accept-Language": "de"})
    en = client.head("/items", headers={"Accept-Language": "en"})

    assert calls == ["GET", "HEAD"]
    assert de.headers["vary"] == "Accept-Language"
    assert en.headers["vary"] == "Accept-Language"


def test_head_with_credentials_bypasses_the_backend() -> None:
    client, calls = _app()
    client.get("/items")

    head = client.head("/items", headers={"Authorization": "Bearer t"})

    assert calls == ["GET", "HEAD"]
    assert head.headers["cache-control"] == "private, max-age=60"


def test_a_dependency_cookie_makes_a_head_hit_private() -> None:
    app = FastAPI()

    def set_cookie(response: Response) -> None:
        response.set_cookie("seen", "1")

    @app.api_route(
        "/items", methods=["GET", "HEAD"], dependencies=[Depends(set_cookie)]
    )
    @cache(ttl=60, public=True)
    async def items() -> dict[str, int]:
        return {"n": 1}

    client = TestClient(app)
    client.get("/items")

    head = client.head("/items")

    assert head.headers["cache-control"] == "private, max-age=60"
    assert head.headers["set-cookie"].startswith("seen=1")


def test_post_still_runs_the_handler() -> None:
    app = FastAPI()
    calls: list[str] = []

    @app.api_route("/items", methods=["GET", "POST"])
    @cache(ttl=60)
    async def items(request: Request) -> dict[str, int]:
        calls.append(request.method)
        return {"n": 1}

    client = TestClient(app)
    client.get("/items")

    post = client.post("/items")

    assert calls == ["GET", "POST"]
    assert "etag" not in post.headers


async def test_head_leaves_a_different_get_entry_alone() -> None:
    app = FastAPI()
    version = {"n": 1}

    @app.api_route("/items", methods=["GET", "HEAD"])
    @cache(ttl=60, no_cache=True)
    async def items() -> dict[str, int]:
        return {"n": version["n"]}

    client = TestClient(app)
    client.get("/items")
    version["n"] = 2

    head = client.head("/items")

    entry = await BackendProxy.get().get(GET_KEY)
    assert entry is not None
    assert entry.content == b'{"n":1}'
    assert head.headers["etag"] != entry.fingerprint


def test_head_with_no_cache_revalidates_against_a_fresh_render() -> None:
    client, calls = _app(no_cache=True)
    etag = client.get("/items").headers["etag"]

    # The handler renders an empty body for HEAD, so its ETag differs.
    head = client.head("/items", headers={"If-None-Match": etag})

    assert calls == ["GET", "HEAD"]
    assert head.status_code == 200
    assert head.headers["cache-control"] == "no-cache"


def test_head_miss_answers_304_when_its_render_matches() -> None:
    client, calls = _app()
    etag = client.head("/items").headers["etag"]

    head = client.head("/items", headers={"If-None-Match": etag})

    assert calls == ["HEAD", "HEAD"]
    assert head.status_code == 304


async def test_head_with_no_store_sends_no_store() -> None:
    client, calls = _app_no_store()

    head = client.head("/items")

    assert calls == ["HEAD"]
    assert head.headers["cache-control"] == "no-store"
    assert await BackendProxy.get().get_all_keys() == []


def _app_no_store() -> tuple[TestClient, list[str]]:
    app = FastAPI()
    calls: list[str] = []

    @app.api_route("/items", methods=["GET", "HEAD"])
    @cache(no_store=True)
    async def items(request: Request) -> dict[str, int]:
        calls.append(request.method)
        return {"n": 1}

    return TestClient(app), calls


def test_head_on_a_private_route_bypasses_the_backend() -> None:
    client, calls = _app(private=True)
    client.get("/items")

    head = client.head("/items")

    assert calls == ["GET", "HEAD"]
    assert head.headers["cache-control"] == "private, max-age=60"
    assert "etag" in head.headers
