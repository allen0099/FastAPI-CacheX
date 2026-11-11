"""``@cache(vary={name: normaliser})`` bounds the entries a header creates (#310)."""

import enum
import hashlib
import warnings
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import invalidate
from fastapi_cachex import invalidate_path
from fastapi_cachex.cache import cache
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.proxy import BackendProxy

BASE_KEY = "http:v2|GET|testserver|/greet|"


def locale(value: str) -> str:
    return "zh-TW" if value.lower().startswith("zh") else "en"


def _app(**cache_kwargs: Any) -> tuple[TestClient, dict[str, int]]:
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/greet")
    @cache(ttl=60, debug_header=True, **cache_kwargs)
    async def greet() -> dict[str, int]:
        calls["n"] += 1
        return {"n": calls["n"]}

    return TestClient(app), calls


async def test_values_the_normaliser_maps_together_share_an_entry() -> None:
    client, calls = _app(vary={"Accept-Language": locale})

    for value in ("en-US,en;q=0.9", "en", "fr", "zh-TW", "ZH-hant;q=0.8"):
        client.get("/greet", headers={"Accept-Language": value})
    client.get("/greet")  # a missing header reaches the normaliser as ""

    assert calls["n"] == 2
    assert sorted(await BackendProxy.get().get_all_keys()) == [
        f"{BASE_KEY}|accept-language=en",
        f"{BASE_KEY}|accept-language=zh-TW",
    ]


def test_the_normaliser_gets_the_trimmed_joined_value() -> None:
    seen: list[str] = []

    def record(value: str) -> str:
        seen.append(value)
        return "x"

    client, _ = _app(vary={"Accept-Language": record})
    client.get(
        "/greet", headers=[("Accept-Language", " de "), ("Accept-Language", "en")]
    )
    client.get("/greet")

    assert seen == ["de,en", ""]


def test_the_vary_response_header_still_names_the_raw_header() -> None:
    """A cache in front of the app cannot run the normaliser."""
    client, _ = _app(vary={"Accept-Language": locale, "Origin": None})

    response = client.get("/greet", headers={"Accept-Language": "en"})

    assert response.headers["Vary"] == "Accept-Language, Origin"


async def test_a_name_mapped_to_none_keeps_the_list_form_key() -> None:
    by_list, _ = _app(vary=["Accept-Language"])
    by_list.get("/greet", headers={"Accept-Language": "de"})
    list_keys = await BackendProxy.get().get_all_keys()
    await BackendProxy.get().clear()

    by_mapping, _ = _app(vary={"Accept-Language": None})
    by_mapping.get("/greet", headers={"Accept-Language": "de"})

    assert await BackendProxy.get().get_all_keys() == list_keys
    assert list_keys == [f"{BASE_KEY}|accept-language=de"]


async def test_an_int_result_and_separator_characters_are_keyed_safely() -> None:
    client, _ = _app(vary={"X-Tier": lambda value: 2, "X-Raw": lambda value: "a|b"})

    client.get("/greet")

    (key,) = await BackendProxy.get().get_all_keys()
    assert key.startswith(f"{BASE_KEY}|x-tier=2|x-raw=a")
    assert key != f"{BASE_KEY}|x-tier=2|x-raw=a|b"  # the separator is escaped


@pytest.mark.parametrize("result", [None, 1.5, True, b"x", ["en"]])
def test_a_normaliser_must_return_a_str_or_an_int(result: object) -> None:
    client, _ = _app(vary={"Accept-Language": lambda value: result})

    with pytest.raises(CacheXError, match="must return a str or an int"):
        client.get("/greet")


def test_an_error_in_the_normaliser_fails_the_request() -> None:
    """It is the application's code, not a backend failure to fail open on."""

    def broken(value: str) -> str:
        msg = "boom"
        raise ValueError(msg)

    client, calls = _app(vary={"Accept-Language": broken})

    with pytest.raises(ValueError, match="boom"):
        client.get("/greet")
    assert calls["n"] == 0


@pytest.mark.parametrize(
    "vary",
    [
        pytest.param({"Accept-Language": "en"}, id="str-value"),
        pytest.param({"Accept-Language": ["en", "de"]}, id="list-value"),
        pytest.param({"": locale}, id="empty-name"),
        pytest.param({"*": locale}, id="star"),
        pytest.param({1: locale}, id="non-str-name"),
    ],
)
def test_an_invalid_mapping_is_rejected_at_decoration(vary: object) -> None:
    with pytest.raises(CacheXError, match="vary"):
        cache(ttl=60, vary=vary)(lambda: None)  # type: ignore[arg-type]


async def test_names_differing_only_in_case_keep_the_first() -> None:
    client, _ = _app(vary={"Accept-Language": locale, "accept-language": None})

    client.get("/greet", headers={"Accept-Language": "fr"})

    assert await BackendProxy.get().get_all_keys() == [f"{BASE_KEY}|accept-language=en"]


async def test_invalidate_applies_the_same_normaliser() -> None:
    app = FastAPI()
    vary = {"Accept-Language": locale}

    @app.get("/greet")
    @cache(ttl=60, vary=vary)
    async def greet() -> dict[str, str]:
        return {}

    @app.post("/drop")
    async def drop(request: Request) -> dict[str, bool]:
        return {
            "raw": await invalidate_path(request, "/greet", vary=["Accept-Language"]),
            "normalised": await invalidate_path(request, "/greet", vary=vary),
        }

    @app.delete("/greet")
    async def drop_get(request: Request) -> bool:
        request.scope["method"] = "GET"
        return await invalidate(request, vary=vary)

    client = TestClient(app)
    client.get("/greet", headers={"Accept-Language": "en-GB"})
    client.get("/greet", headers={"Accept-Language": "zh-TW"})

    # "fr" is another raw value, and the same normalised one as "en-GB".
    dropped = client.post("/drop", headers={"Accept-Language": "fr"}).json()

    assert dropped == {"raw": False, "normalised": True}
    assert await BackendProxy.get().get_all_keys() == [
        f"{BASE_KEY}|accept-language=zh-TW"
    ]
    assert client.delete("/greet", headers={"Accept-Language": "zh"}).json() is True


async def test_a_normalised_credential_header_is_still_hashed() -> None:
    """The key is listed by the monitoring routes: no cookie value in it."""

    def theme(cookie: str) -> str:
        return "dark" if "theme=dark" in cookie else "light"

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a normalised Cookie does not warn
        client, calls = _app(vary={"Cookie": theme})

    client.get("/greet", headers={"Cookie": "sid=1; theme=dark"})
    client.get("/greet", headers={"Cookie": "sid=2; theme=dark"})

    digest = hashlib.sha256(b"dark").hexdigest()
    assert await BackendProxy.get().get_all_keys() == [
        f"{BASE_KEY}|cookie=sha256:{digest}"
    ]
    assert calls["n"] == 1


def test_cookie_without_a_normaliser_still_warns() -> None:
    with pytest.warns(UserWarning, match="cache vary on Cookie"):
        cache(ttl=60, vary={"Cookie": None})(lambda: None)


def test_an_async_normaliser_is_rejected_at_decoration() -> None:
    async def pick(value: str) -> str:
        return value

    with pytest.raises(CacheXError, match="async normaliser is not supported"):
        cache(ttl=60, vary={"Accept-Language": pick})(lambda: None)  # type: ignore[dict-item]


def test_a_normaliser_that_returns_a_coroutine_is_named_as_such() -> None:
    """A lambda around an async function passes the check at decoration."""

    async def pick(value: str) -> str:
        return value

    client, _ = _app(
        vary={"Accept-Language": lambda value: pick(value)}  # noqa: PLW0108
    )

    # filterwarnings = error: an unclosed coroutine would fail this test.
    with pytest.raises(CacheXError, match="async normaliser is not supported"):
        client.get("/greet")


async def test_enum_members_are_keyed_on_their_value() -> None:
    class Locale(str, enum.Enum):
        EN = "en"

    class Tier(enum.IntEnum):
        GOLD = 3

    client, _ = _app(
        vary={"X-Locale": lambda value: Locale.EN, "X-Tier": lambda value: Tier.GOLD}
    )

    client.get("/greet")

    assert await BackendProxy.get().get_all_keys() == [
        f"{BASE_KEY}|x-locale=en|x-tier=3"
    ]


def test_the_normaliser_is_not_called_for_requests_that_skip_the_cache() -> None:
    seen: list[str] = []

    def record(value: str) -> str:
        seen.append(value)
        return "x"

    app = FastAPI()

    @app.api_route("/greet", methods=["GET", "HEAD", "POST"])
    @cache(ttl=60, vary={"Accept-Language": record})
    async def greet() -> dict[str, str]:
        return {}

    client = TestClient(app)
    client.post("/greet")
    client.get("/greet", headers={"Authorization": "Bearer t"})
    assert seen == []

    client.get("/greet")
    client.head("/greet")
    assert seen == ["", ""]
