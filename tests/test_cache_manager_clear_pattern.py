"""CacheManager.clear_pattern() with glob metacharacters in the key prefix."""

import warnings
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from typing import Any

import pytest
import pytest_asyncio

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.manager import CacheManager
from fastapi_cachex.types import CacheEntry
from tests.live_servers import MEMCACHED_SERVER
from tests.live_servers import REDIS_HOST
from tests.live_servers import REDIS_PORT
from tests.live_servers import requires_memcached
from tests.live_servers import requires_redis
from tests.live_servers import requires_redis_package

if TYPE_CHECKING:
    from fastapi_cachex.backends.base import BaseCacheBackend

_SLOW_PATH_WARNING = r"clear_pattern\(\) will list every key"
_ENTRY = CacheEntry(fingerprint="x", content=b"1")


def _globby_manager(backend: "BaseCacheBackend", key_prefix: str) -> CacheManager:
    """Build a manager whose prefix holds glob characters, expecting the warning."""
    with pytest.warns(UserWarning, match=_SLOW_PATH_WARNING):
        return CacheManager(backend=backend, key_prefix=key_prefix)


@pytest_asyncio.fixture(
    params=[
        pytest.param("memory", id="MemoryBackend"),
        pytest.param(
            "redis",
            id="RedisBackend",
            marks=[requires_redis, requires_redis_package],
        ),
    ]
)
async def backend(request: Any) -> AsyncGenerator["BaseCacheBackend", Any]:
    """A memory backend, or a live Redis one when it is available."""
    if request.param == "memory":
        mem_backend = MemoryBackend()
        mem_backend.start_cleanup()
        yield mem_backend
        await mem_backend.clear()
        mem_backend.stop_cleanup()
        return

    from fastapi_cachex.backends import AsyncRedisCacheBackend

    redis_backend = AsyncRedisCacheBackend(
        host=REDIS_HOST,
        port=REDIS_PORT,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
        key_prefix="test_cache_manager_clear_pattern:",
    )
    await redis_backend.clear()
    yield redis_backend
    await redis_backend.clear()


# --- Constructor warning -------------------------------------------------------


@pytest.mark.parametrize("key_prefix", ["cache[1]:", "a?:", "a*:", "a]:", "a\\:"])
def test_glob_prefix_warns_at_the_callers_line(
    memory_backend: MemoryBackend, key_prefix: str
) -> None:
    """A prefix with a glob metacharacter warns, pointing at the caller."""
    with pytest.warns(UserWarning, match=_SLOW_PATH_WARNING) as record:
        CacheManager(backend=memory_backend, key_prefix=key_prefix)

    assert len(record) == 1
    assert record[0].filename == __file__


@pytest.mark.parametrize("key_prefix", ["cache:", "", "my-app.v2:"])
def test_plain_prefix_does_not_warn(
    memory_backend: MemoryBackend, key_prefix: str
) -> None:
    """A prefix without glob metacharacters constructs silently."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        CacheManager(backend=memory_backend, key_prefix=key_prefix)


# --- Fast path -------------------------------------------------------------------


async def test_plain_prefix_delegates_to_backend_clear_pattern(
    memory_backend: MemoryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a plain prefix, the backend's own clear_pattern gets prefix + pattern."""
    manager = CacheManager(backend=memory_backend, key_prefix="cache:")
    calls: list[str] = []

    async def fake_clear_pattern(pattern: str) -> int:
        calls.append(pattern)
        return 7

    async def no_get_all_keys() -> list[str]:  # pragma: no cover - must not run
        pytest.fail("the fast path must not enumerate keys")

    monkeypatch.setattr(memory_backend, "clear_pattern", fake_clear_pattern)
    monkeypatch.setattr(memory_backend, "get_all_keys", no_get_all_keys)

    assert await manager.clear_pattern("user:*") == 7
    assert calls == ["cache:user:*"]


# --- Slow path -------------------------------------------------------------------


async def test_bracket_prefix_clears_its_own_keys(
    backend: "BaseCacheBackend",
) -> None:
    """key_prefix="cache[1]:" is literal, so clear_pattern("*") finds its keys."""
    manager = _globby_manager(backend, "cache[1]:")
    await manager.set("a", 1)
    await manager.set("b", 2)
    await backend.set("cache1:a", _ENTRY)

    removed = await manager.clear_pattern("*")

    assert removed == 2
    assert not await manager.has("a")
    assert not await manager.has("b")
    assert await backend.get("cache1:a") is not None


async def test_question_mark_prefix_leaves_other_namespaces_alone(
    backend: "BaseCacheBackend",
) -> None:
    """key_prefix="a?:" does not clear the keys of a manager with prefix "ab:"."""
    globby = _globby_manager(backend, "a?:")
    other = CacheManager(backend=backend, key_prefix="ab:")
    await globby.set("x", 1)
    await other.set("x", 2)

    removed = await globby.clear_pattern("*")

    assert removed == 1
    assert not await globby.has("x")
    assert await other.get("x") == 2


async def test_slow_path_pattern_is_still_a_glob(
    backend: "BaseCacheBackend",
) -> None:
    """Metacharacters in the pattern part stay live on the slow path."""
    manager = _globby_manager(backend, "c[1]:")
    for key in ("user:1", "user:2", "user:10", "User:3", "post:1"):
        await manager.set(key, key)

    assert await manager.clear_pattern("user:?") == 2
    assert await manager.clear_pattern("[!u]*") == 2  # "User:3", "post:1"
    assert await manager.get("user:10") == "user:10"
    assert await manager.clear_pattern("user:*") == 1


async def test_slow_path_matches_the_remainder_not_the_whole_key(
    memory_backend: MemoryBackend,
) -> None:
    """The prefix must match literally at the start of the key."""
    manager = _globby_manager(memory_backend, "*:")
    await manager.set("k", 1)
    await memory_backend.set("x:k", _ENTRY)

    assert await manager.clear_pattern("*") == 1
    assert await memory_backend.get("x:k") is not None


# --- Memcached ---------------------------------------------------------------------


@requires_memcached
@pytest.mark.parametrize(
    ("key_prefix", "globby"),
    [pytest.param("cache:", False, id="fast"), pytest.param("c[1]:", True, id="slow")],
)
async def test_memcached_returns_zero_with_one_runtime_warning(
    key_prefix: str, globby: bool
) -> None:
    """Both paths return 0 on Memcached with exactly one RuntimeWarning."""
    from fastapi_cachex.backends import MemcachedBackend

    backend = MemcachedBackend(servers=[MEMCACHED_SERVER])
    manager = (
        _globby_manager(backend, key_prefix)
        if globby
        else CacheManager(backend=backend, key_prefix=key_prefix)
    )

    with pytest.warns(RuntimeWarning) as record:
        removed = await manager.clear_pattern("*")

    assert removed == 0
    assert len(record) == 1
