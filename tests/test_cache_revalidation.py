"""Tests for RFC 9110 conditional requests: If-None-Match parsing and 304 headers.

`If-None-Match` was compared as one exact string against the server's own ETag,
so a client holding several validators (`W/"a", W/"b"`) or sending `*` never
revalidated successfully. The 304 also dropped the caching headers the matching
200 carried, which RFC 9110 §15.4.5 requires it to repeat.
"""

import pytest
from fastapi import FastAPI
from fastapi import Response
from fastapi.testclient import TestClient

from fastapi_cachex.cache import _etag_matches
from fastapi_cachex.cache import cache


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, False),
        ("", False),
        ('W/"abc"', True),
        ('"abc"', True),
        ('w/"abc"', True),
        ('W/"other"', False),
        ('W/"x", W/"abc", W/"y"', True),
        ('"x","abc"', True),
        ('W/"x", W/"y"', False),
        ("*", True),
        ("  *  ", True),
        ('W/"*"', False),
    ],
)
def test_etag_matches(header: str | None, expected: bool) -> None:
    """Weak comparison, multi-value lists and `*` all resolve per §8.8.3.2."""
    assert _etag_matches(header, 'W/"abc"') is expected


def _etag_app() -> FastAPI:
    app = FastAPI()

    @app.get("/doc")
    @cache(ttl=60)
    async def doc():
        return Response(
            content="body",
            media_type="text/plain",
            headers={
                "Vary": "Accept-Encoding",
                "Expires": "Wed, 21 Oct 2026 07:28:00 GMT",
            },
        )

    return app


def test_one_of_several_client_etags_matches():
    """A browser holding multiple validators still gets a 304."""
    client = TestClient(_etag_app())
    etag = client.get("/doc").headers["ETag"]

    response = client.get(
        "/doc", headers={"If-None-Match": f'W/"stale", {etag}, W/"older"'}
    )

    assert response.status_code == 304


def test_strong_form_of_a_weak_etag_matches():
    """If-None-Match compares weakly, so the `W/` prefix must not matter."""
    client = TestClient(_etag_app())
    etag = client.get("/doc").headers["ETag"]

    response = client.get("/doc", headers={"If-None-Match": etag.removeprefix("W/")})

    assert response.status_code == 304


def test_star_matches_any_cached_entry():
    """`If-None-Match: *` means "any representation", so a stored entry is a match."""
    client = TestClient(_etag_app())
    client.get("/doc")

    response = client.get("/doc", headers={"If-None-Match": "*"})

    assert response.status_code == 304


def test_unrelated_etag_still_serves_the_body():
    """A validator the server does not recognise must not short-circuit to 304."""
    client = TestClient(_etag_app())
    client.get("/doc")

    response = client.get("/doc", headers={"If-None-Match": 'W/"nothing-like-it"'})

    assert response.status_code == 200
    assert response.text == "body"


def test_not_modified_repeats_the_caching_headers():
    """§15.4.5: the 304 carries the fields the 200 used to steer caches."""
    client = TestClient(_etag_app())
    first = client.get("/doc")

    response = client.get("/doc", headers={"If-None-Match": first.headers["ETag"]})

    assert response.status_code == 304
    assert response.headers["Vary"] == first.headers["Vary"]
    assert response.headers["Expires"] == first.headers["Expires"]
    assert response.headers["ETag"] == first.headers["ETag"]
    assert response.headers["Cache-Control"] == first.headers["Cache-Control"]


def test_not_modified_from_a_fresh_render_repeats_the_headers():
    """The no-cache path revalidates against fresh content and must do the same."""
    app = FastAPI()

    @app.get("/fresh")
    @cache(ttl=60, no_cache=True)
    async def fresh():
        return Response(
            content="body",
            media_type="text/plain",
            headers={"Vary": "Accept-Language"},
        )

    client = TestClient(app)
    first = client.get("/fresh")

    response = client.get("/fresh", headers={"If-None-Match": first.headers["ETag"]})

    assert response.status_code == 304
    assert response.headers["Vary"] == "Accept-Language"


def test_private_not_modified_repeats_the_headers():
    """Private responses never touch the backend but still revalidate properly."""
    app = FastAPI()

    @app.get("/me")
    @cache(ttl=30, private=True)
    async def me():
        return Response(
            content="body",
            media_type="text/plain",
            headers={"Vary": "Authorization"},
        )

    client = TestClient(app)
    first = client.get("/me")

    response = client.get(
        "/me", headers={"If-None-Match": f'W/"stale", {first.headers["ETag"]}'}
    )

    assert response.status_code == 304
    assert response.headers["Vary"] == "Authorization"
