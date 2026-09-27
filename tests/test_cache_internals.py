"""The ``@cache`` wrapper builds a key only when it touches the backend (#182)."""

import pytest
from fastapi import FastAPI
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex.cache import _build_cache_control
from fastapi_cachex.cache import cache
from fastapi_cachex.cache import default_key_builder


def _app_counting_keys(**cache_kwargs: object) -> tuple[TestClient, list[str]]:
    calls: list[str] = []

    def counting_builder(request: Request) -> str:
        calls.append(request.url.path)
        return default_key_builder(request)

    app = FastAPI()

    @app.api_route("/item", methods=["GET", "POST"])
    @cache(key_builder=counting_builder, **cache_kwargs)  # type: ignore[arg-type]
    async def item() -> dict[str, str]:
        return {"ok": "yes"}

    return TestClient(app), calls


@pytest.mark.parametrize(
    "cache_kwargs",
    [
        pytest.param({"ttl": 60, "no_store": True}, id="no_store"),
        pytest.param({"ttl": 60, "private": True}, id="private"),
        pytest.param({}, id="no-ttl"),
        pytest.param({"ttl": 0}, id="ttl-0"),
    ],
)
def test_key_builder_not_called_when_the_backend_is_skipped(
    cache_kwargs: dict[str, object],
) -> None:
    """Routes that never read or write the backend do not build a key."""
    client, calls = _app_counting_keys(**cache_kwargs)

    first = client.get("/item")
    client.get("/item", headers={"If-None-Match": first.headers.get("etag", "x")})

    assert first.status_code == 200
    assert calls == []


def test_key_builder_not_called_for_non_get() -> None:
    """Only GET is cached, so other methods do not build a key."""
    client, calls = _app_counting_keys(ttl=60)

    assert client.post("/item").status_code == 200
    assert calls == []


def test_key_builder_called_once_per_cached_request() -> None:
    """A cached route still builds its key on every GET, miss and hit alike."""
    client, calls = _app_counting_keys(ttl=60)

    miss = client.get("/item")
    hit = client.get("/item")
    not_modified = client.get("/item", headers={"If-None-Match": miss.headers["etag"]})

    assert (miss.status_code, hit.status_code, not_modified.status_code) == (
        200,
        200,
        304,
    )
    assert calls == ["/item", "/item", "/item"]


_NO_FLAGS = {
    "ttl": None,
    "stale": None,
    "stale_ttl": None,
    "no_cache": False,
    "public": False,
    "private": False,
    "immutable": False,
    "must_revalidate": False,
}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({}, "", id="nothing"),
        pytest.param(
            {
                "ttl": 60,
                "public": True,
                "must_revalidate": True,
                "stale": "revalidate",
                "stale_ttl": 30,
                "immutable": True,
            },
            "public, max-age=60, must-revalidate, stale-while-revalidate=30, immutable",
            id="directive-order",
        ),
        pytest.param(
            {"ttl": 60, "private": True, "stale": "error", "stale_ttl": 5},
            "private, max-age=60, stale-if-error=5",
            id="private-stale-if-error",
        ),
        pytest.param(
            {"no_cache": True, "ttl": 60, "public": True, "must_revalidate": True},
            "no-cache, must-revalidate",
            id="no-cache-drops-the-rest",
        ),
    ],
)
def test_build_cache_control(overrides: dict[str, object], expected: str) -> None:
    """The header is a pure function of the decorator arguments."""
    assert _build_cache_control(**{**_NO_FLAGS, **overrides}) == expected  # type: ignore[arg-type]
