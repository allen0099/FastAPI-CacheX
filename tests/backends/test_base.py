"""The non-abstract helpers on ``BaseCacheBackend`` must work for third-party
subclasses that only implement the abstract methods."""

from typing import Any

import pytest

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry


class DictBackend(BaseCacheBackend):
    """Minimal backend implementing only the abstract interface."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[CacheEntry, int | None]] = {}

    async def get(self, key: str) -> CacheEntry | None:
        item = self.store.get(key)
        return None if item is None else item[0]

    async def set(self, key: str, value: CacheEntry, ttl: int | None = None) -> None:
        self.store[key] = (value, ttl)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def clear(self) -> None:
        self.store.clear()

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        return 0

    async def clear_pattern(self, pattern: str) -> int:
        return 0

    async def get_all_keys(self) -> list[str]:
        return list(self.store)

    async def get_cache_data(self) -> dict[str, tuple[Any, float | None]]:
        return {key: (value, None) for key, (value, _) in self.store.items()}


@pytest.fixture
def backend() -> DictBackend:
    return DictBackend()


@pytest.mark.asyncio
async def test_increment_fallback_creates_then_adds(backend: DictBackend) -> None:
    assert await backend.increment("hits", ttl=30) == 1
    assert await backend.increment("hits", 4, ttl=30) == 5
    assert await backend.increment("hits", -2) == 3

    assert backend.store["hits"] == (counter_entry(3), None)


@pytest.mark.asyncio
async def test_increment_fallback_rejects_a_cached_response(
    backend: DictBackend,
) -> None:
    await backend.set("page", CacheEntry(fingerprint="e", content=b"<html>"))

    with pytest.raises(CacheXError, match="not a counter"):
        await backend.increment("page")


@pytest.mark.asyncio
async def test_get_and_delete_fallback_returns_then_removes(
    backend: DictBackend,
) -> None:
    value = CacheEntry(fingerprint="e", content=b"once")
    await backend.set("once", value)

    assert await backend.get_and_delete("once") == value
    assert "once" not in backend.store
    assert await backend.get_and_delete("once") is None
