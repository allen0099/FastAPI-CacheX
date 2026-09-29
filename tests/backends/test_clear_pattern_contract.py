"""The `clear_pattern` contract must mean the same thing on every backend.

`MemoryBackend` used to glob the path component of an HTTP cache key while
Redis globbed the whole key, so `clear_pattern("/users/*")` cleared entries in
development and silently cleared nothing in production.
"""

import warnings
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.manager import CacheManager
from fastapi_cachex.types import CacheEntry
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import redis_skip_reason

KEYS = (
    "http:v2|GET|localhost|/users/1|",
    "http:v2|GET|localhost|/users/2|",
    "http:v2|POST|localhost|/users/1|",
    "http:v2|GET|localhost|/posts/1|",
    "cache:user:1",
    "cache:post:1",
)


@pytest_asyncio.fixture
async def memory() -> AsyncGenerator[MemoryBackend, Any]:
    """A memory backend whose cleanup task is stopped after the test."""
    backend = MemoryBackend()
    yield backend
    backend.stop_cleanup()


@pytest_asyncio.fixture
async def redis() -> AsyncGenerator[AsyncRedisCacheBackend, Any]:
    """A Redis backend, or a skip when no server is reachable."""
    reason = redis_skip_reason()
    if reason is not None:
        pytest.skip(reason)

    backend = AsyncRedisCacheBackend(
        host=REDIS_HOST,
        port=REDIS_PORT,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
    )
    # Clear on the way in as well as out: a run that was interrupted (or a
    # manual experiment) leaves keys under this prefix behind, and the tests
    # here assert exact key counts over it.
    await backend.clear()
    yield backend
    await backend.clear()


@pytest.fixture
def backend(request: pytest.FixtureRequest) -> BaseCacheBackend:
    """Either backend, so one test body states the shared contract."""
    return request.getfixturevalue(request.param)  # type: ignore[no-any-return]


async def _populate(backend: BaseCacheBackend) -> None:
    for key in KEYS:
        await backend.set(key, CacheEntry(fingerprint=key, content=b"v"))


@pytest.mark.parametrize("backend", ["memory", "redis"], indirect=True)
@pytest.mark.parametrize(
    ("pattern", "expected_removed"),
    [
        ("http:v2|GET|*|/users/*", 2),
        ("*|localhost|/users/*", 3),
        ("cache:*", 2),
        ("cache:user:*", 1),
        # `*` is an unrestricted glob on both backends: it spans the separator,
        # so this reaches the host component too.
        ("*|/users/*", 3),
    ],
)
async def test_clear_pattern_globs_the_whole_key(
    backend: BaseCacheBackend, pattern: str, expected_removed: int
) -> None:
    """Both backends remove exactly the same entries for the same pattern."""
    await _populate(backend)

    removed = await backend.clear_pattern(pattern)

    assert removed == expected_removed
    assert len(await backend.get_all_keys()) == len(KEYS) - expected_removed


@pytest.mark.parametrize("backend", ["memory", "redis"], indirect=True)
async def test_a_bare_path_pattern_warns_instead_of_clearing_nothing(
    backend: BaseCacheBackend,
) -> None:
    """Zero cleared is indistinguishable from an empty cache, so say something."""
    await _populate(backend)

    with pytest.warns(RuntimeWarning, match="clear_path"):
        removed = await backend.clear_pattern("/users/*")

    assert removed == 0


@pytest.mark.parametrize("backend", ["memory", "redis"], indirect=True)
@pytest.mark.parametrize("path", ["/users/*", "/users/1"])
async def test_the_pattern_the_warning_suggests_clears_the_path(
    backend: BaseCacheBackend, path: str
) -> None:
    """The whole-key pattern in the warning matches the GET entries it names."""
    await _populate(backend)
    await backend.set("http:v2|GET|localhost|/users/1|page=2", CacheEntry("e", b"v"))

    with pytest.warns(RuntimeWarning) as record:
        await backend.clear_pattern(path)
    suggested = str(record[0].message).rsplit("'", 2)[-2]

    removed = await backend.clear_pattern(suggested)

    assert removed == (3 if path == "/users/*" else 2)
    assert "http:v2|POST|localhost|/users/1|" in await backend.get_all_keys()


@pytest.mark.parametrize("backend", ["memory", "redis"], indirect=True)
async def test_the_0_3_cleanup_pattern_leaves_current_keys(
    backend: BaseCacheBackend,
) -> None:
    """``clear_pattern("*|||*")`` from the migration guide removes 0.3.x keys only."""
    await _populate(backend)
    await backend.set("GET|||localhost|||/users/1|||", CacheEntry("e", b"v"))
    await backend.set("GET|||localhost|||/users/1|||page=2", CacheEntry("e", b"v"))

    assert await backend.clear_pattern("*|||*") == 2
    assert sorted(await backend.get_all_keys()) == sorted(KEYS)


@pytest.mark.parametrize("backend", ["memory", "redis"], indirect=True)
@pytest.mark.parametrize(
    "pattern", ["http:v2|GET|*|/users/*", "cache:user:*", "*", "user:*", "/users/*"]
)
async def test_patterns_that_clear_something_do_not_warn(
    backend: BaseCacheBackend, pattern: str
) -> None:
    """A pattern that did its job is not worth warning about.

    That includes a path-shaped one: keys stored directly through `set` really
    can be paths, and `clear_path` matches those too.
    """
    await _populate(backend)
    await backend.set("/users/1", CacheEntry(fingerprint="e", content=b"v"))

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        await backend.clear_pattern(pattern)


@pytest.mark.parametrize("backend", ["memory", "redis"], indirect=True)
async def test_cache_manager_clear_pattern_is_relative_to_its_namespace(
    backend: BaseCacheBackend,
) -> None:
    """`CacheManager` keys carry no separators, so they behave the same either way."""
    manager = CacheManager(backend=backend)
    await manager.set("user:1", {"name": "a"})
    await manager.set("user:2", {"name": "b"})
    await manager.set("post:1", {"title": "c"})

    assert await manager.clear_pattern("user:*") == 2
    assert await manager.get("user:1") is None
    assert await manager.get("post:1") == {"title": "c"}


@pytest.mark.parametrize("backend", ["memory", "redis"], indirect=True)
async def test_clear_path_finds_paths_with_encoded_characters(
    backend: BaseCacheBackend,
) -> None:
    """``clear_path`` takes the decoded path and matches the encoded key."""
    entry = CacheEntry(fingerprint="etag", content=b"x")
    # Keys as default_key_builder writes them for "/a|b/100%" and a neighbour.
    await backend.set("http:v2|GET|h|/a%7Cb/100%25|", entry)
    await backend.set("http:v2|GET|h|/a%7Cb/100%25|v=1", entry)
    await backend.set("http:v2|GET|h|/a|b/100%25|", entry)

    assert await backend.clear_path("/a|b/100%") == 1
    assert await backend.clear_path("/a|b/100%", include_params=True) == 1
    assert await backend.get_all_keys() == ["http:v2|GET|h|/a|b/100%25|"]


@pytest.mark.parametrize("backend", ["memory", "redis"], indirect=True)
async def test_clear_path_finds_keys_with_extra_components(
    backend: BaseCacheBackend,
) -> None:
    """Keys from ``build_cache_key(request, ...)`` are cleared by their path (#264)."""
    entry = CacheEntry(fingerprint="etag", content=b"x")
    await backend.set("http:v2|GET|h|/me|", entry)
    await backend.set("http:v2|GET|h|/me||user-1", entry)
    await backend.set("http:v2|GET|h|/me||user-1|de", entry)
    await backend.set("http:v2|GET|h|/me|page=2|user-1", entry)
    # The path appears elsewhere in these keys, but not as the path.
    await backend.set("http:v2|GET|h|/other||/me|", entry)
    await backend.set("http:v2|GET|/me|/other|", entry)

    assert await backend.clear_path("/me") == 3
    assert await backend.clear_path("/me", include_params=True) == 1
    assert sorted(await backend.get_all_keys()) == [
        "http:v2|GET|/me|/other|",
        "http:v2|GET|h|/other||/me|",
    ]
