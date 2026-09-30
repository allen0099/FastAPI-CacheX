"""A header sent more than once is stored and replayed line by line (#105)."""

import json

import pytest
from fastapi import FastAPI
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.datastructures import MutableHeaders

from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.backends.codec import encode_entry
from fastapi_cachex.cache import cache
from fastapi_cachex.headers import add_vary
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

LINES = [
    (b"link", b"</a.css>; rel=preload"),
    (b"x-tag", b"b"),
    (b"link", b"</b.js>; rel=preload"),
    (b"x-tag", b"a, c"),
]


def _key(path: str) -> str:
    return f"http:v2|GET|testserver|{path}|"


def _response() -> Response:
    response = Response(content="ok", media_type="text/plain")
    response.raw_headers.extend(LINES)
    return response


def _app(**options: object) -> tuple[TestClient, list[int]]:
    app = FastAPI()
    calls: list[int] = []

    @app.get("/r")
    @cache(ttl=60, **options)  # type: ignore[arg-type]
    async def endpoint() -> Response:
        calls.append(1)
        return _response()

    return TestClient(app), calls


async def test_every_line_is_stored_in_order() -> None:
    client, _ = _app()

    client.get("/r")
    entry = await BackendProxy.get().get(_key("/r"))

    assert entry is not None
    assert entry.headers == tuple((n.decode(), v.decode()) for n, v in LINES)


def test_a_hit_replays_every_line() -> None:
    client, calls = _app()

    miss = client.get("/r")
    hit = client.get("/r")

    assert calls == [1]
    for response in (miss, hit):
        assert response.headers.get_list("link") == [
            "</a.css>; rel=preload",
            "</b.js>; rel=preload",
        ]
        # Not joined: a value holding a comma stays one line.
        assert response.headers.get_list("x-tag") == ["b", "a, c"]


def _vary_app(**options: object) -> TestClient:
    app = FastAPI()

    @app.get("/v")
    @cache(ttl=60, **options)  # type: ignore[arg-type]
    async def endpoint() -> Response:
        response = Response(content="ok", media_type="text/plain")
        response.raw_headers.extend([(b"vary", b"Accept"), (b"vary", b"Origin")])
        return response

    return TestClient(app)


@pytest.mark.parametrize(
    "options",
    [{}, {"no_cache": True}, {"private": True}],
    ids=["stored-entry", "no-cache", "private"],
)
def test_a_304_repeats_every_vary_line(options: dict[str, object]) -> None:
    client = _vary_app(**options)
    etag = client.get("/v").headers["etag"]

    not_modified = client.get("/v", headers={"If-None-Match": etag})

    assert not_modified.status_code == 304
    assert not_modified.headers.get_list("vary") == ["Accept", "Origin"]


@pytest.mark.parametrize(
    "options",
    [{}, {"no_cache": True}, {"private": True}],
    ids=["stored-entry", "no-cache", "private"],
)
def test_vary_names_are_added_after_every_line(options: dict[str, object]) -> None:
    """``vary=`` adds its names on a new line instead of rewriting the first."""
    client = _vary_app(vary=["X-Tenant"], **options)

    miss = client.get("/v")
    hit = client.get("/v")
    not_modified = client.get("/v", headers={"If-None-Match": miss.headers["etag"]})

    assert not_modified.status_code == 304
    for response in (miss, hit, not_modified):
        assert response.headers.get_list("vary") == ["Accept", "Origin", "X-Tenant"]


async def test_a_hit_sets_its_own_framing_and_validators() -> None:
    """An entry built outside ``@cache`` cannot duplicate or override them."""
    client, calls = _app()
    await BackendProxy.get().set(
        _key("/r"),
        CacheEntry(
            'W/"stored"',
            b"ok",
            media_type="text/plain",
            headers=(
                ("etag", 'W/"other"'),
                ("cache-control", "no-store"),
                ("content-type", "text/html"),
                ("content-length", "99"),
                ("x-kept", "1"),
            ),
            stored_at=None,
        ),
    )

    hit = client.get("/r")

    assert calls == []
    assert hit.headers.get_list("etag") == ['W/"stored"']
    assert hit.headers.get_list("cache-control") == ["max-age=60"]
    assert hit.headers.get_list("content-type") == ["text/plain; charset=utf-8"]
    assert hit.headers.get_list("content-length") == ["2"]
    assert hit.headers["x-kept"] == "1"


def test_the_codec_round_trips_repeated_lines() -> None:
    entry = CacheEntry("f", b"x", headers=(("link", "</a>"), ("link", "</b>")))

    raw = encode_entry(entry)

    assert json.loads(raw)["headers"] == [["link", "</a>"], ["link", "</b>"]]
    assert decode_entry(raw) == entry


def test_the_codec_reads_the_0_3_object() -> None:
    raw = json.dumps({"fingerprint": "f", "content": "x", "headers": {"x-a": "1"}})

    entry = decode_entry(raw)

    assert entry is not None
    assert entry.headers == (("x-a", "1"),)


@pytest.mark.parametrize(
    "headers",
    [
        "x-a",
        5,
        [["x-a"]],
        [["x-a", 1]],
        [[1, "v"]],
        {"x-a": 1},
        ["ab"],
        [{"x": 1, "y": 2}],
    ],
    ids=[
        "string",
        "number",
        "short-pair",
        "int-value",
        "int-name",
        "int-in-object",
        "two-char-string",
        "object-pair",
    ],
)
def test_the_codec_treats_malformed_headers_as_a_miss(headers: object) -> None:
    raw = json.dumps({"fingerprint": "f", "content": "x", "headers": headers})

    assert decode_entry(raw) is None


@pytest.mark.parametrize(
    "headers",
    [None, {"x-a": "1"}, [("x-a", "1")], [["x-a", "1"]], iter([("x-a", "1")])],
    ids=["none", "mapping", "list-of-tuples", "list-of-lists", "iterator"],
)
def test_cache_entry_converts_headers_to_a_tuple(headers: object) -> None:
    entry = CacheEntry("f", b"x", headers=headers)  # type: ignore[arg-type]

    expected = () if headers is None else (("x-a", "1"),)
    assert entry.headers == expected


def test_cache_entry_rejects_a_non_str_value() -> None:
    with pytest.raises(TypeError, match=r"\(str, str\) pairs"):
        CacheEntry("f", b"x", headers=[("x-a", 1)])  # type: ignore[arg-type]


def test_add_vary_adds_no_line_when_every_name_is_there() -> None:
    headers = MutableHeaders(raw=[(b"vary", b"Accept"), (b"vary", b"Origin")])

    add_vary(headers, ["origin", "ACCEPT"])

    assert headers.getlist("vary") == ["Accept", "Origin"]
