"""``@cache(vary=[...])`` keys on request headers and sends ``Vary`` (#268)."""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex import build_cache_key
from fastapi_cachex import invalidate
from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.proxy import BackendProxy

BASE_KEY = "GET|||testserver|||/greet|||"


def _app(**cache_kwargs: Any) -> tuple[TestClient, dict[str, int]]:
    app = FastAPI()
    calls = {"n": 0}

    @app.api_route("/greet", methods=["GET", "POST"])
    @cache(ttl=60, **cache_kwargs)
    async def greet(request: Request) -> dict[str, Any]:
        calls["n"] += 1
        return {"lang": request.headers.get("accept-language"), "n": calls["n"]}

    return TestClient(app), calls


async def test_each_header_value_gets_its_own_entry() -> None:
    client, calls = _app(vary=["Accept-Language"])

    de = client.get("/greet", headers={"Accept-Language": "de"})
    en = client.get("/greet", headers={"Accept-Language": "en"})
    de_again = client.get("/greet", headers={"Accept-Language": "de"})

    assert de.json() == {"lang": "de", "n": 1}
    assert en.json() == {"lang": "en", "n": 2}
    assert de_again.json() == {"lang": "de", "n": 1}
    assert calls["n"] == 2
    assert sorted(await BackendProxy.get().get_all_keys()) == [
        f"{BASE_KEY}|||accept-language=de",
        f"{BASE_KEY}|||accept-language=en",
    ]


async def test_routes_without_vary_keep_their_key() -> None:
    client, _ = _app()

    response = client.get("/greet", headers={"Accept-Language": "de"})

    assert await BackendProxy.get().get_all_keys() == [BASE_KEY]
    assert "vary" not in response.headers


async def test_values_are_trimmed_joined_and_escaped() -> None:
    client, _ = _app(vary=["accept-language", "X-Variant"])

    client.get("/greet", headers={"Accept-Language": "  de  "})
    client.get(
        "/greet",
        headers=[
            ("Accept-Language", "fr"),
            ("Accept-Language", " en "),
            ("X-Variant", "a|||b"),
        ],
    )

    assert sorted(await BackendProxy.get().get_all_keys()) == [
        f"{BASE_KEY}|||accept-language=de|||x-variant=",
        f"{BASE_KEY}|||accept-language=fr,en|||x-variant=a%7C%7C%7Cb",
    ]


async def test_missing_and_empty_headers_share_an_entry() -> None:
    client, calls = _app(vary=["Accept-Language"])

    client.get("/greet")
    client.get("/greet", headers={"Accept-Language": ""})

    assert calls["n"] == 1
    assert await BackendProxy.get().get_all_keys() == [f"{BASE_KEY}|||accept-language="]


async def test_vary_components_follow_a_custom_key_builder() -> None:
    def per_tenant(request: Request) -> str:
        return build_cache_key(request, "tenant-1")

    client, _ = _app(vary=["Accept-Language"], key_builder=per_tenant)
    client.get("/greet", headers={"Accept-Language": "de"})

    assert await BackendProxy.get().get_all_keys() == [
        f"{BASE_KEY}|||tenant-1|||accept-language=de"
    ]


async def test_clear_path_clears_every_variant() -> None:
    client, _ = _app(vary=["Accept-Language"])
    for lang in ("de", "en", "fr"):
        client.get("/greet", headers={"Accept-Language": lang})
    client.get("/greet?page=2", headers={"Accept-Language": "de"})

    backend = BackendProxy.get()
    assert await backend.clear_path("/greet") == 3
    assert await backend.clear_path("/greet", include_params=True) == 1


async def test_invalidate_deletes_the_requested_variant() -> None:
    app = FastAPI()

    @app.get("/greet")
    @cache(ttl=60, vary=["Accept-Language"])
    async def greet(request: Request) -> dict[str, str | None]:
        return {"lang": request.headers.get("accept-language")}

    @app.post("/greet")
    async def reset(request: Request) -> dict[str, bool]:
        request.scope["method"] = "GET"
        return {
            "without_vary": await invalidate(request),
            "with_vary": await invalidate(request, vary=["Accept-Language"]),
        }

    client = TestClient(app)
    client.get("/greet", headers={"Accept-Language": "de"})
    client.get("/greet", headers={"Accept-Language": "en"})

    result = client.post("/greet", headers={"Accept-Language": "de"}).json()

    assert result == {"without_vary": False, "with_vary": True}
    assert await BackendProxy.get().get_all_keys() == [
        f"{BASE_KEY}|||accept-language=en"
    ]


def test_vary_header_on_miss_hit_and_304() -> None:
    client, _ = _app(vary=["Accept-Language"])
    headers = {"Accept-Language": "de"}

    miss = client.get("/greet", headers=headers)
    hit = client.get("/greet", headers=headers)
    not_modified = client.get(
        "/greet", headers={**headers, "If-None-Match": miss.headers["etag"]}
    )

    assert not_modified.status_code == 304
    for response in (miss, hit, not_modified):
        assert response.headers["vary"] == "Accept-Language"


@pytest.mark.parametrize(
    ("cache_kwargs", "request_headers"),
    [
        pytest.param({"private": True}, {}, id="private"),
        pytest.param({"no_store": True}, {}, id="no_store"),
        pytest.param({"no_cache": True}, {}, id="no_cache"),
        pytest.param({}, {"Authorization": "Bearer a"}, id="authorization-bypass"),
    ],
)
def test_vary_header_on_responses_that_skip_the_backend(
    cache_kwargs: dict[str, Any], request_headers: dict[str, str]
) -> None:
    client, _ = _app(vary=["Accept-Language"], **cache_kwargs)

    first = client.get("/greet", headers=request_headers)
    revalidated = (
        client.get(
            "/greet",
            headers={**request_headers, "If-None-Match": first.headers["etag"]},
        )
        if "etag" in first.headers
        else first
    )

    for response in (first, revalidated):
        assert response.headers["vary"] == "Accept-Language"


def test_authorization_bypass_keeps_private_cache_control() -> None:
    client, _ = _app(vary=["Accept-Language"], public=False)

    response = client.get("/greet", headers={"Authorization": "Bearer a"})

    assert response.headers["cache-control"] == "private, max-age=60"
    assert response.headers["vary"] == "Accept-Language"


async def test_unstored_cookie_response_gets_vary_and_private() -> None:
    app = FastAPI()

    @app.get("/greet")
    @cache(ttl=60, public=True, vary=["Accept-Language"])
    async def greet(response: Response) -> dict[str, str]:
        response.set_cookie("seen", "1")
        return {"ok": "yes"}

    response = TestClient(app).get("/greet")

    assert response.headers["vary"] == "Accept-Language"
    assert response.headers["cache-control"] == "private, max-age=60"
    assert await BackendProxy.get().get_all_keys() == []


def test_names_already_in_vary_are_not_repeated() -> None:
    app = FastAPI()

    @app.get("/greet")
    @cache(ttl=60, vary=["accept-language", "Accept"])
    async def greet(response: Response) -> dict[str, str]:
        response.headers["Vary"] = "Accept-Language, Origin"
        return {"ok": "yes"}

    client = TestClient(app)
    miss = client.get("/greet")
    hit = client.get("/greet")

    for response in (miss, hit):
        assert response.headers.get_list("vary") == ["Accept-Language, Origin, Accept"]


def test_vary_star_is_left_alone() -> None:
    app = FastAPI()

    @app.get("/greet")
    @cache(ttl=60, vary=["Accept-Language"])
    async def greet(response: Response) -> dict[str, str]:
        response.headers["Vary"] = "*"
        return {"ok": "yes"}

    client = TestClient(app)

    assert client.get("/greet").headers.get_list("vary") == ["*"]
    assert client.get("/greet").headers.get_list("vary") == ["*"]


def test_non_get_requests_get_no_vary() -> None:
    client, _ = _app(vary=["Accept-Language"])

    assert "vary" not in client.post("/greet").headers


def test_duplicate_names_are_listed_once() -> None:
    client, _ = _app(vary=["Accept-Language", "accept-language"])

    assert client.get("/greet").headers["vary"] == "Accept-Language"


@pytest.mark.parametrize(
    "vary",
    [
        pytest.param("Accept", id="bare-str"),
        pytest.param(b"Accept", id="bytes"),
        pytest.param({"Accept"}, id="set"),
        pytest.param([""], id="empty-name"),
        pytest.param(["Accept Language"], id="space"),
        pytest.param(["Accept,Origin"], id="comma"),
        pytest.param([None], id="none"),
        pytest.param(["*"], id="star"),
    ],
)
def test_invalid_vary_is_rejected_at_decoration(vary: object) -> None:
    with pytest.raises(CacheXError, match="vary"):
        cache(ttl=60, vary=vary)(lambda: None)  # type: ignore[arg-type]


async def test_invalid_vary_is_rejected_by_invalidate() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    with pytest.raises(CacheXError, match="vary"):
        await invalidate(request, vary="Accept")
