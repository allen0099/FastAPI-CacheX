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

    async def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None

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


async def test_increment_fallback_creates_then_adds(backend: DictBackend) -> None:
    assert await backend.increment("hits", ttl=30) == 1
    assert await backend.increment("hits", 4, ttl=30) == 5
    assert await backend.increment("hits", -2) == 3

    assert backend.store["hits"] == (counter_entry(3), None)


async def test_increment_fallback_rejects_a_cached_response(
    backend: DictBackend,
) -> None:
    await backend.set("page", CacheEntry(fingerprint="e", content=b"<html>"))

    with pytest.raises(CacheXError, match="not a counter"):
        await backend.increment("page")


async def test_get_and_delete_fallback_returns_then_removes(
    backend: DictBackend,
) -> None:
    value = CacheEntry(fingerprint="e", content=b"once")
    await backend.set("once", value)

    assert await backend.get_and_delete("once") == value
    assert "once" not in backend.store
    assert await backend.get_and_delete("once") is None


async def test_fallbacks_lose_to_a_delete_between_get_and_delete(
    backend: DictBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another caller removed the key after the get: only it got the entry."""
    value = CacheEntry(fingerprint="e", content=b"once")
    await backend.set("once", value)

    async def lost_the_race(key: str) -> bool:
        backend.store.pop(key, None)
        return False

    monkeypatch.setattr(backend, "delete", lost_the_race)

    assert await backend.get_and_delete("once") is None
    await backend.set("once", value)
    assert await backend.delete_if_equals("once", value) is False


async def test_delete_many_fallback_deletes_one_by_one(backend: DictBackend) -> None:
    await backend.set("a", CacheEntry(fingerprint="e", content=b"1"))
    await backend.set("b", CacheEntry(fingerprint="e", content=b"2"))

    assert await backend.delete_many(["a", "b", "missing"]) == 2
    assert backend.store == {}


async def test_set_if_absent_fallback_stores_only_the_first_value(
    backend: DictBackend,
) -> None:
    first = CacheEntry(fingerprint="lock", content=b"owner-a")
    second = CacheEntry(fingerprint="lock", content=b"owner-b")

    assert await backend.set_if_absent("slot", first, ttl=30) is True
    assert await backend.set_if_absent("slot", second, ttl=30) is False
    assert backend.store["slot"] == (first, 30)


async def test_delete_if_equals_fallback_removes_only_a_matching_entry(
    backend: DictBackend,
) -> None:
    mine = CacheEntry(fingerprint="lock", content=b"owner-a")
    theirs = CacheEntry(fingerprint="lock", content=b"owner-b")
    await backend.set("slot", theirs)

    assert await backend.delete_if_equals("slot", mine) is False
    assert "slot" in backend.store
    assert await backend.delete_if_equals("slot", theirs) is True
    assert "slot" not in backend.store
    assert await backend.delete_if_equals("slot", theirs) is False


async def test_expire_if_equals_fallback_updates_ttl_only_when_matching(
    backend: DictBackend,
) -> None:
    mine = CacheEntry(fingerprint="lock", content=b"owner-a")
    theirs = CacheEntry(fingerprint="lock", content=b"owner-b")
    await backend.set("slot", theirs, ttl=30)

    assert await backend.expire_if_equals("slot", mine, ttl=60) is False
    assert backend.store["slot"] == (theirs, 30)
    assert await backend.expire_if_equals("slot", theirs, ttl=60) is True
    assert backend.store["slot"] == (theirs, 60)
    assert await backend.expire_if_equals("missing", theirs, ttl=60) is False


async def test_set_if_equals_fallback_stores_only_over_a_matching_entry(
    backend: DictBackend,
) -> None:
    mine = CacheEntry(fingerprint="session", content=b"v1")
    theirs = CacheEntry(fingerprint="session", content=b"v2")
    new = CacheEntry(fingerprint="session", content=b"v3")
    await backend.set("slot", theirs, ttl=30)

    assert await backend.set_if_equals("slot", mine, new, ttl=60) is False
    assert backend.store["slot"] == (theirs, 30)
    assert await backend.set_if_equals("slot", theirs, new, ttl=60) is True
    assert backend.store["slot"] == (new, 60)
    assert await backend.set_if_equals("missing", theirs, new) is False
    assert "missing" not in backend.store


async def test_aclose_is_a_no_op_by_default(backend: DictBackend) -> None:
    await backend.set("key", CacheEntry(fingerprint="f", content=b"v"))

    await backend.aclose()
    await backend.aclose()

    assert "key" in backend.store


class ClosingBackend(DictBackend):
    """Counts `aclose()` calls."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


async def test_async_with_returns_the_backend_and_closes_it() -> None:
    backend = ClosingBackend()

    async with backend as entered:
        assert entered is backend
        assert backend.closed == 0

    assert backend.closed == 1


async def test_async_with_closes_the_backend_when_the_body_raises() -> None:
    backend = ClosingBackend()

    async def fail_inside() -> None:
        async with backend:
            msg = "boom"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        await fail_inside()

    assert backend.closed == 1


class LegacyDictBackend(DictBackend):
    """A backend written against 0.3.x, whose ``delete`` returns ``None``."""

    async def delete(self, key: str) -> None:  # type: ignore[override]
        self.store.pop(key, None)


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda b, e: b.get_and_delete("k"), "entry"),
        (lambda b, e: b.delete_if_equals("k", e), True),
        (lambda b, e: b.delete_many(["k"]), 1),
    ],
    ids=["get_and_delete", "delete_if_equals", "delete_many"],
)
async def test_fallbacks_count_a_none_delete_as_removed_and_warn(
    call: Any, expected: object
) -> None:
    """0.3.x semantics until 0.5.0: the key is gone, so the caller won."""
    backend = LegacyDictBackend()
    entry = CacheEntry(fingerprint="e", content=b"v")
    await backend.set("k", entry)

    with pytest.warns(
        FutureWarning, match=r"LegacyDictBackend\.delete\(\) returned None"
    ):
        result = await call(backend, entry)

    assert result == (entry if expected == "entry" else expected)
    assert "k" not in backend.store
