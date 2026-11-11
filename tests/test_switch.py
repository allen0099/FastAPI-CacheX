"""``set_enabled(False)`` and ``FASTAPI_CACHEX_DISABLED`` turn caching off (#557)."""

import importlib
import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheManager
from fastapi_cachex import cache
from fastapi_cachex import cached
from fastapi_cachex import is_enabled
from fastapi_cachex import set_enabled
from fastapi_cachex import switch
from fastapi_cachex.backends import MemoryBackend


@pytest.fixture
def off() -> Iterator[None]:
    set_enabled(False)
    yield
    set_enabled(True)


def _stored() -> list[str]:
    backend = BackendProxy.get()
    assert isinstance(backend, MemoryBackend)
    return list(backend.cache)


def _client() -> tuple[TestClient, list[int]]:
    app = FastAPI()
    runs: list[int] = []

    @app.get("/items")
    @cache(ttl=60, debug_header=True)
    async def items() -> dict[str, int]:
        runs.append(1)
        return {"n": 1}

    return TestClient(app), runs


def test_on_by_default() -> None:
    assert is_enabled() is True


def test_a_cached_route_runs_every_time_and_stores_nothing(off: None) -> None:
    client, runs = _client()

    for _ in range(2):
        response = client.get("/items")
        assert response.headers["x-cache"] == "BYPASS"
        assert response.headers["x-cache-reason"] == "disabled"

    assert len(runs) == 2
    assert _stored() == []


def test_a_cached_route_sends_the_same_headers_as_with_caching_on() -> None:
    client, _ = _client()
    on = client.get("/items")
    set_enabled(False)
    try:
        off = client.get("/items")
        revalidated = client.get(
            "/items", headers={"If-None-Match": on.headers["etag"]}
        )
        with_credentials = client.get("/items", headers={"Authorization": "Bearer t"})
    finally:
        set_enabled(True)

    assert off.headers["cache-control"] == on.headers["cache-control"]
    assert off.headers["etag"] == on.headers["etag"]
    assert off.json() == on.json()
    assert revalidated.status_code == 304
    # Still private, and named as the credentials it is refused for.
    assert "private" in with_credentials.headers["cache-control"]
    assert with_credentials.headers["x-cache-reason"] == "authorization"


def test_turning_it_back_on_caches_again(off: None) -> None:
    client, runs = _client()
    client.get("/items")

    set_enabled(True)

    assert client.get("/items").headers["x-cache"] == "MISS"
    assert client.get("/items").headers["x-cache"] == "HIT"
    assert len(runs) == 2


async def test_get_or_set_runs_the_factory_and_neither_reads_nor_writes(
    off: None,
) -> None:
    manager = CacheManager()
    await manager.set("report", "stored")
    before = _stored()
    runs: list[int] = []

    def build() -> tuple[int, int]:
        runs.append(1)
        return (1, 2)

    # The shape of a hit: a tuple comes back as a list, also with the lock.
    assert await manager.get_or_set("report", build, tags=["reports"]) == [1, 2]
    assert await manager.get_or_set("report", build, type_=tuple[int, int]) == (1, 2)

    assert len(runs) == 2
    # No value, lock or tag version was written; explicit storage still reads.
    assert _stored() == before
    assert await manager.get("report") == "stored"


async def test_a_cached_function_runs_every_time(off: None) -> None:
    runs: list[int] = []

    @cached(ttl=60)
    async def total(n: int) -> int:
        runs.append(n)
        return n * 2

    assert await total(2) == 4
    assert await total(2) == 4
    assert runs == [2, 2]
    assert _stored() == []


def test_set_enabled_takes_only_a_bool() -> None:
    with pytest.raises(TypeError, match="must be a bool"):
        set_enabled("false")  # type: ignore[arg-type]
    assert is_enabled() is True


@pytest.mark.parametrize(
    ("value", "enabled"),
    [
        ("1", False),
        ("TRUE", False),
        (" on ", False),
        ("yes", False),
        ("0", True),
        ("", True),
        ("false", True),
    ],
)
def test_the_environment_variable_is_read_at_import(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    value: str,
    enabled: bool,
) -> None:
    monkeypatch.setenv("FASTAPI_CACHEX_DISABLED", value)
    try:
        with caplog.at_level(logging.WARNING, logger="fastapi_cachex.switch"):
            importlib.reload(switch)
        # The names imported elsewhere read the reloaded module's state.
        assert is_enabled() is enabled
        assert ("Caching is off" in caplog.text) is not enabled
    finally:
        monkeypatch.delenv("FASTAPI_CACHEX_DISABLED")
        importlib.reload(switch)
    assert is_enabled() is True
