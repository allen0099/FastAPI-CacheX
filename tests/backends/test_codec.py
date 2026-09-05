"""Unit tests for the entry codec shared by the Redis and Memcached backends."""

import json as std_json
import types

import pytest

from fastapi_cachex.backends import codec
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry


@pytest.fixture
def stdlib_json(monkeypatch):
    """Swap orjson for the standard library so ``dumps`` returns ``str``."""
    monkeypatch.setattr(
        codec,
        "json",
        types.SimpleNamespace(dumps=std_json.dumps, loads=std_json.loads),
    )


def test_encode_entry_round_trips_arbitrary_bytes():
    entry = CacheEntry(
        fingerprint="etag", content=b"\x00\xff\x80 bytes", media_type="x/y"
    )

    assert codec.decode_entry(codec.encode_entry(entry)) == entry


def test_encode_entry_returns_bytes_with_stdlib_json(stdlib_json):
    entry = CacheEntry(fingerprint="etag", content=b"payload")
    encoded = codec.encode_entry(entry)

    assert isinstance(encoded, bytes)
    assert codec.decode_entry(encoded) == entry
    assert codec.decode_entry(encoded.decode()) == entry


def test_decode_entry_none_is_a_miss():
    assert codec.decode_entry(None) is None


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        b'{"some": "data"}',
        '{"fingerprint": "e", "content": [1, 2, 3]}',
        '"a bare string"',
        "[]",
    ],
)
def test_decode_entry_treats_malformed_documents_as_a_miss(raw):
    assert codec.decode_entry(raw) is None


@pytest.mark.parametrize("raw", ["7", b"7", b"7   ", " 7\n"])
def test_decode_entry_reads_a_bare_integer_as_a_counter(raw):
    assert codec.decode_entry(raw) == counter_entry(7)


def test_decode_entry_reads_a_negative_counter():
    assert codec.decode_entry("-2") == counter_entry(-2)
