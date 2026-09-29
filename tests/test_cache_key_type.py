"""``CacheKey`` is the one encoder and decoder of HTTP cache keys (#270)."""

import dataclasses

import pytest
from fastapi import Request

import fastapi_cachex
from fastapi_cachex import CacheKey
from fastapi_cachex import build_cache_key
from fastapi_cachex.types import CACHE_KEY_SEPARATOR

SEP = CACHE_KEY_SEPARATOR


def _request(
    path: str = "/items", query: bytes = b"", host: str = "example.com"
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": [(b"host", host.encode())],
        }
    )


def test_cache_key_is_exported() -> None:
    assert "CacheKey" in fastapi_cachex.__all__
    assert fastapi_cachex.CacheKey is CacheKey


@pytest.mark.parametrize(
    ("args", "sort_query"),
    [
        ((), False),
        (("tenant", 7), False),
        (("a|b", "100%"), True),
    ],
)
def test_from_request_is_what_build_cache_key_returns(
    args: tuple[str | int, ...], sort_query: bool
) -> None:
    request = _request("/p|q", b"b=2&a=1", "Host|||x")

    key = CacheKey.from_request(request, *args, sort_query=sort_query)

    assert key.to_str() == build_cache_key(request, *args, sort_query=sort_query)
    assert key.extra == tuple(str(arg) for arg in args)


def test_from_request_sorts_the_query_only_when_asked() -> None:
    request = _request(query=b"b=2&a=1")

    assert CacheKey.from_request(request).query == "b=2&a=1"
    assert CacheKey.from_request(request, sort_query=True).query == "a=1&b=2"


def test_build_cache_key_format_is_pinned() -> None:
    request = _request("/p|q", b"b=2&a=1", "Host|||x")

    assert build_cache_key(request, "a|b", 100, sort_query=True) == (
        "GET|||Host%7C%7C%7Cx|||/p%7Cq|||a=1&b=2|||a%7Cb|||100"
    )


def test_to_str_escapes_every_component_but_the_query() -> None:
    key = CacheKey("G|T%", "evil|||host", "/p|||/100%", "q=1", ("a|||b", "%7C"))

    assert key.to_str() == SEP.join(
        [
            "G%7CT%25",
            "evil%7C%7C%7Chost",
            "/p%7C%7C%7C/100%25",
            "q=1",
            "a%7C%7C%7Cb",
            "%257C",
        ]
    )


@pytest.mark.parametrize(
    "key",
    [
        CacheKey("GET", "example.com", "/items"),
        CacheKey("GET", "evil|||host", "/p|||/100%", "q=1&r=%7C", ("a|||b", "", "3")),
        CacheKey("GET", "[::1]:8000", "/", "", ("only-extra",)),
    ],
)
def test_parse_reverses_to_str(key: CacheKey) -> None:
    assert CacheKey.parse(key.to_str()) == key


def test_parse_reads_a_key_that_ends_at_the_path() -> None:
    assert CacheKey.parse(f"GET{SEP}example.com{SEP}/items") == CacheKey(
        "GET", "example.com", "/items", ""
    )


@pytest.mark.parametrize(
    "key",
    [
        "cache:user:1",
        "oauth_state:abc",
        f"GET{SEP}example.com",
        f"{SEP}example.com{SEP}/items{SEP}",
    ],
)
def test_parse_returns_none_for_keys_that_are_not_http_keys(key: str) -> None:
    assert CacheKey.parse(key) is None


def test_query_cannot_contain_the_separator() -> None:
    with pytest.raises(ValueError, match=r"CacheKey\.query cannot contain"):
        CacheKey("GET", "h", "/", f"a{SEP}b")


def test_a_method_with_the_separator_is_escaped_not_rejected() -> None:
    request = Request(
        {
            "type": "http",
            "method": "A|||B",
            "path": "/p",
            "raw_path": b"/p",
            "query_string": b"",
            "headers": [(b"host", b"h")],
        }
    )

    key = build_cache_key(request)

    assert key == SEP.join(["A%7C%7C%7CB", "h", "/p", ""])
    assert CacheKey.parse(key) == CacheKey("A|||B", "h", "/p")


def test_cache_key_is_frozen() -> None:
    key = CacheKey("GET", "h", "/")

    with pytest.raises(dataclasses.FrozenInstanceError):
        key.path = "/other"  # type: ignore[misc]


def test_path_glob_matches_the_escaped_path_literally() -> None:
    assert CacheKey.path_glob("/files/[draft]*?\\|x%") == (
        f"*{SEP}/files/\\[draft\\]\\*\\?\\\\%7Cx%25{SEP}*"
    )
