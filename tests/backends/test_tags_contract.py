"""Tag invalidation behaves the same on every backend (#538).

It is built on ``get_many``, ``set_many`` and ``set_if_absent`` only, so it
needs no key enumeration: Memcached included.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from fastapi_cachex import CacheManager
from fastapi_cachex import _tags
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.types import CacheEntry
from tests.backends.test_batch_contract import FACTORIES

TAGS = ("contract-users", "contract-user:1", "contract tag with space")
KEYS = ("contract:a", "contract:b", "contract:c")


@pytest_asyncio.fixture(params=list(FACTORIES))
async def manager(request: pytest.FixtureRequest) -> AsyncIterator[CacheManager]:
    factory, skip_reason = FACTORIES[request.param]
    reason = skip_reason()
    if reason is not None:
        pytest.skip(reason)
    backend = factory()
    owned = [f"cache:{key}" for key in KEYS] + [f"tag:{tag}" for tag in TAGS]
    await backend.delete_many(owned)
    yield CacheManager(backend)
    await backend.delete_many(owned)


async def test_a_value_is_a_miss_once_one_of_its_tags_is_invalidated(
    manager: CacheManager,
) -> None:
    await manager.set("contract:a", 1, ttl=60, tags=[TAGS[0], TAGS[1]])
    await manager.set("contract:b", 2, ttl=60, tags=[TAGS[0]])
    await manager.set("contract:c", 3, ttl=60)
    assert await manager.get_many(KEYS) == {
        "contract:a": 1,
        "contract:b": 2,
        "contract:c": 3,
    }

    await manager.invalidate_tags(TAGS[1])

    assert await manager.get("contract:a", "gone") == "gone"
    assert await manager.get_many(KEYS) == {"contract:b": 2, "contract:c": 3}
    # Written again, under the tag's new version, it is served again.
    await manager.set("contract:a", 4, ttl=60, tags=[TAGS[0], TAGS[1]])
    assert await manager.get("contract:a") == 4


async def test_a_tag_name_the_server_would_refuse_as_a_key_works(
    manager: CacheManager,
) -> None:
    await manager.set("contract:a", 1, ttl=60, tags=[TAGS[2]])

    await manager.invalidate_tags(TAGS[2])

    assert await manager.get("contract:a") is None


async def test_a_tag_that_lost_its_version_invalidates_what_it_tagged(
    manager: CacheManager,
) -> None:
    """Evicted, or the server restarted: a miss, never an invalidated value."""
    await manager.set("contract:a", 1, ttl=60, tags=[TAGS[0]])

    await manager.backend.delete(f"tag:{TAGS[0]}")

    assert await manager.get("contract:a") is None


async def test_versions_are_created_once_and_then_read(manager: CacheManager) -> None:
    first = await _tags.current_versions(manager.backend, TAGS[:2])
    again = await _tags.current_versions(manager.backend, TAGS[:2])

    assert [name for name, _ in first] == list(TAGS[:2])
    assert first == again
    await _tags.invalidate(manager.backend, [TAGS[0]])
    after = dict(await _tags.current_versions(manager.backend, TAGS[:2]))
    assert after[TAGS[0]] != dict(first)[TAGS[0]]
    assert after[TAGS[1]] == dict(first)[TAGS[1]]


async def test_get_or_set_does_not_serve_a_value_computed_before_an_invalidation(
    manager: CacheManager,
) -> None:
    """The versions are read before the factory runs."""
    calls = 0

    async def load() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            # The data changes while this value is being computed from it.
            await manager.invalidate_tags(TAGS[0])
        return calls

    assert await manager.get_or_set("contract:a", load, ttl=60, tags=[TAGS[0]]) == 1
    assert await manager.get_or_set("contract:a", load, ttl=60, tags=[TAGS[0]]) == 2
    assert await manager.get_or_set("contract:a", load, ttl=60, tags=[TAGS[0]]) == 2


class _RacingBackend(MemoryBackend):
    """Another writer creates the tag's version first, or it is gone again."""

    def __init__(self, winner: CacheEntry | None) -> None:
        super().__init__()
        self.winner = winner

    async def set_if_absent(
        self, key: str, value: CacheEntry, ttl: object = None
    ) -> bool:
        if self.winner is not None:
            await self.set(key, self.winner)
        return False


async def test_two_writers_creating_a_tag_agree_on_the_first_version() -> None:
    backend = _RacingBackend(CacheEntry(fingerprint="tag", content=b"theirs"))

    assert await _tags.current_versions(backend, ["users"]) == (("users", "theirs"),)


async def test_a_version_that_cannot_be_read_back_matches_nothing() -> None:
    backend = _RacingBackend(None)

    ((name, version),) = await _tags.current_versions(backend, ["users"])

    assert name == "users"
    assert version
    assert await backend.get("tag:users") is None
