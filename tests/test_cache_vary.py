"""``@cache(vary=[...])`` keys on request headers and sends ``Vary`` (#268, #312)."""

import hashlib
import warnings
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex import add_routes
from fastapi_cachex import build_cache_key
from fastapi_cachex import invalidate
from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.proxy import BackendProxy

BASE_KEY = "http:v2|GET|testserver|/greet|"


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
        f"{BASE_KEY}|accept-language=de",
        f"{BASE_KEY}|accept-language=en",
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
        f"{BASE_KEY}|accept-language=de|x-variant=",
        f"{BASE_KEY}|accept-language=fr,en|x-variant=a%7C%7C%7Cb",
    ]


async def test_missing_and_empty_headers_share_an_entry() -> None:
    client, calls = _app(vary=["Accept-Language"])

    client.get("/greet")
    client.get("/greet", headers={"Accept-Language": ""})

    assert calls["n"] == 1
    assert await BackendProxy.get().get_all_keys() == [f"{BASE_KEY}|accept-language="]


async def test_vary_components_follow_a_custom_key_builder() -> None:
    def per_tenant(request: Request) -> str:
        return build_cache_key(request, "tenant-1")

    client, _ = _app(vary=["Accept-Language"], key_builder=per_tenant)
    client.get("/greet", headers={"Accept-Language": "de"})

    assert await BackendProxy.get().get_all_keys() == [
        f"{BASE_KEY}|tenant-1|accept-language=de"
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
    assert await BackendProxy.get().get_all_keys() == [f"{BASE_KEY}|accept-language=en"]


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


@pytest.mark.filterwarnings("ignore:cache no_store ignores:UserWarning")
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
        assert response.headers.get_list("vary") == [
            "Accept-Language, Origin",
            "Accept",
        ]


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


# --- Credential headers are hashed (#312) ---

TOKEN_A = "Bearer secret-token-a"
TOKEN_B = "Bearer secret-token-b"
ME_KEY = "http:v2|GET|testserver|/me|"


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _credential_app(
    vary: list[str], **cache_kwargs: Any
) -> tuple[TestClient, dict[str, int]]:
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/me")
    @cache(ttl=60, vary=vary, **cache_kwargs)
    async def me() -> dict[str, int]:
        calls["n"] += 1
        return {"n": calls["n"]}

    @app.post("/me")
    async def reset(request: Request) -> dict[str, bool]:
        request.scope["method"] = "GET"
        return {"deleted": await invalidate(request, vary=vary)}

    add_routes(app, prefix="/cache", dependencies=[])
    return TestClient(app), calls


async def test_authorization_value_is_hashed_everywhere_the_key_shows() -> None:
    client, calls = _credential_app(["Authorization"], cache_authorized=True)

    first = client.get("/me", headers={"Authorization": TOKEN_A})
    again = client.get("/me", headers={"Authorization": TOKEN_A})
    other = client.get("/me", headers={"Authorization": TOKEN_B})

    assert first.json() == again.json() == {"n": 1}
    assert other.json() == {"n": 2}
    assert calls["n"] == 2
    keys = await BackendProxy.get().get_all_keys()
    assert sorted(keys) == sorted(
        [
            f"{ME_KEY}|authorization={_digest(TOKEN_A)}",
            f"{ME_KEY}|authorization={_digest(TOKEN_B)}",
        ]
    )
    records = client.get("/cache/cached-records").text
    hits = client.get("/cache/cached-hits").text
    for shown in (" ".join(keys), records, hits):
        assert "secret-token" not in shown
        assert _digest(TOKEN_A) in shown


@pytest.mark.parametrize(
    ("name", "header"),
    [
        pytest.param("Authorization", "authorization", id="authorization"),
        pytest.param("AUTHORIZATION", "authorization", id="upper-case"),
        pytest.param("proxy-authorization", "proxy-authorization", id="proxy"),
        pytest.param("X-Session-Token", "x-session-token", id="session-token"),
    ],
)
async def test_credential_headers_are_hashed_in_any_case(
    name: str, header: str
) -> None:
    client, _ = _credential_app([name], public=True)

    client.get("/me", headers={header.upper(): "  s3cret  "})

    assert await BackendProxy.get().get_all_keys() == [
        f"{ME_KEY}|{header}={_digest('s3cret')}"
    ]


async def test_cookie_is_hashed_with_repeated_lines_joined() -> None:
    with pytest.warns(UserWarning, match="cache vary on Cookie"):
        client, _ = _credential_app(["Cookie"])

    client.get("/me", headers=[("Cookie", "sid=abc"), ("Cookie", " theme=dark ")])

    assert await BackendProxy.get().get_all_keys() == [
        f"{ME_KEY}|cookie={_digest('sid=abc,theme=dark')}"
    ]


async def test_missing_or_empty_credential_header_gives_the_empty_component() -> None:
    client, calls = _credential_app(["Authorization"], cache_authorized=True)

    client.get("/me")
    client.get("/me", headers={"Authorization": "   "})

    assert calls["n"] == 1
    assert await BackendProxy.get().get_all_keys() == [f"{ME_KEY}|authorization="]


async def test_authorization_in_vary_still_bypasses_without_opt_in() -> None:
    client, calls = _credential_app(["Authorization"])

    client.get("/me", headers={"Authorization": TOKEN_A})
    client.get("/me", headers={"Authorization": TOKEN_A})
    client.get("/me")

    assert calls["n"] == 3
    assert await BackendProxy.get().get_all_keys() == [f"{ME_KEY}|authorization="]


async def test_non_credential_headers_stay_readable() -> None:
    client, _ = _credential_app(["X-Tenant", "Authorization"], cache_authorized=True)

    client.get("/me", headers={"X-Tenant": "acme", "Authorization": TOKEN_A})

    assert await BackendProxy.get().get_all_keys() == [
        f"{ME_KEY}|x-tenant=acme|authorization={_digest(TOKEN_A)}"
    ]


async def test_invalidate_deletes_the_hashed_variant() -> None:
    client, _ = _credential_app(["Authorization"], cache_authorized=True)
    client.get("/me", headers={"Authorization": TOKEN_A})
    client.get("/me", headers={"Authorization": TOKEN_B})

    deleted = client.post("/me", headers={"Authorization": TOKEN_A}).json()
    again = client.post("/me", headers={"Authorization": TOKEN_A}).json()

    assert deleted == {"deleted": True}
    assert again == {"deleted": False}
    assert await BackendProxy.get().get_all_keys() == [
        f"{ME_KEY}|authorization={_digest(TOKEN_B)}"
    ]


# --- vary=["Cookie"] warns at decoration (#312) ---


@pytest.mark.parametrize("name", ["Cookie", "cookie", "COOKIE"])
def test_vary_on_cookie_warns_at_the_callers_line(name: str) -> None:
    with pytest.warns(UserWarning, match="cache vary on Cookie") as record:

        @cache(ttl=60, vary=["Accept-Language", name])
        async def handler() -> dict[str, str]:
            return {}

    [warning] = record
    assert warning.filename == __file__
    assert "build_cache_key" in str(warning.message)
    assert "private=True" in str(warning.message)


def test_documented_filter_silences_the_cookie_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warnings.filterwarnings("ignore", message="cache vary on Cookie")

        cache(ttl=60, vary=["Cookie"])(lambda: None)


@pytest.mark.parametrize(
    "vary",
    [
        pytest.param(["Accept-Language"], id="accept-language"),
        pytest.param(
            ["Authorization", "Proxy-Authorization", "X-Session-Token"],
            id="credentials",
        ),
        pytest.param(["X-Cookie-Consent"], id="cookie-lookalike"),
    ],
)
def test_other_vary_names_do_not_warn(vary: list[str]) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")

        cache(ttl=60, vary=vary)(lambda: None)
