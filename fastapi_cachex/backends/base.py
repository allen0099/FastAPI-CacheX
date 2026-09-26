"""Base cache backend interface and abstract implementation."""

import warnings
from abc import ABC
from abc import abstractmethod
from collections.abc import Iterable
from typing import Any

from fastapi_cachex.types import CACHE_KEY_SEPARATOR
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import counter_entry
from fastapi_cachex.types import counter_value


def warn_if_path_shaped(pattern: str, cleared: int) -> None:
    """Warn when a ``clear_pattern`` that cleared nothing was written as a path.

    Matching happens against the whole key, so a pattern like ``/users/*``
    cannot match an HTTP cache entry and the call quietly reports zero cleared
    — indistinguishable from a cache that was already empty, which is exactly
    the outcome the caller was trying to avoid. Only the combination of "looks
    like a bare path" and "matched nothing" warns, so keys that really are
    paths (stored directly through ``set``) stay silent when they work.
    """
    if cleared == 0 and pattern.startswith("/") and CACHE_KEY_SEPARATOR not in pattern:
        warnings.warn(
            f"clear_pattern({pattern!r}) cleared nothing. Patterns match whole "
            f"cache keys, which look like 'method{CACHE_KEY_SEPARATOR}host"
            f"{CACHE_KEY_SEPARATOR}path{CACHE_KEY_SEPARATOR}query', so a bare "
            "path matches no HTTP cache entry. Use clear_path(path, "
            "include_params=True) to clear by path, or write the whole key out "
            f"as 'GET{CACHE_KEY_SEPARATOR}*{CACHE_KEY_SEPARATOR}{pattern}'.",
            RuntimeWarning,
            stacklevel=3,
        )


# The largest TTL accepted anywhere: 2**31 - 1 seconds, about 68 years.
MAX_TTL = 2**31 - 1


def validate_ttl(ttl: int | None) -> int | None:
    """Return ``ttl`` if it is ``None`` or a positive number of seconds.

    Every backend reads ``0`` or a negative TTL differently (Memcached treats
    ``0`` as "never expires", Redis rejects it, the memory backend expires the
    entry at once), so the library refuses them instead of letting the
    meaning depend on the backend. ``None`` is the way to say "no expiry".

    Only an ``int`` is a TTL. A ``float`` worked on the memory backend and
    failed on Redis and Memcached, and ``True`` passed as one second.

    Raises:
        TypeError: If ``ttl`` is not an ``int`` (``bool`` included)
        ValueError: If ``ttl`` is zero, negative or larger than ``MAX_TTL``
    """
    if ttl is None:
        return None
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        msg = f"ttl must be an int number of seconds or None, got {type(ttl).__name__}"
        raise TypeError(msg)
    if ttl <= 0:
        msg = f"ttl must be a positive number of seconds or None, got {ttl!r}"
        raise ValueError(msg)
    if ttl > MAX_TTL:
        msg = f"ttl must be at most {MAX_TTL} seconds (about 68 years)"
        raise ValueError(msg)
    return ttl


def validate_delta(delta: int) -> int:
    """Return ``delta`` if it is an ``int`` a counter can be changed by.

    Redis counters are signed 64-bit integers and Memcached's are unsigned, so
    a delta outside the signed 64-bit range fails on both, where it used to be
    reported as the key not holding a counter.

    Raises:
        TypeError: If ``delta`` is not an ``int`` (``bool`` included)
        ValueError: If ``delta`` does not fit in a signed 64-bit integer
    """
    if isinstance(delta, bool) or not isinstance(delta, int):
        msg = f"delta must be an int, got {type(delta).__name__}"
        raise TypeError(msg)
    if not -(2**63) <= delta < 2**63:
        msg = "delta must fit in a signed 64-bit integer"
        raise ValueError(msg)
    return delta


class BaseCacheBackend(ABC):
    """Base class for all cache backends."""

    @abstractmethod
    async def get(self, key: str) -> CacheEntry | None:
        """Retrieve a cached response."""

    @abstractmethod
    async def set(self, key: str, value: CacheEntry, ttl: int | None = None) -> None:
        """Store a response in the cache.

        ``ttl`` is ``None`` (never expires) or a positive number of seconds;
        implementations should pass it through ``validate_ttl`` so zero and
        negative values are rejected the same way on every backend.
        """

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove a response from the cache."""

    async def delete_many(self, keys: Iterable[str]) -> int:
        """Remove every key in ``keys``; returns how many were removed.

        The base implementation deletes one key at a time and reports how
        many were attempted, since ``delete`` does not say whether the key
        existed. The built-in backends override it and count what was
        actually removed.
        """
        count = 0
        for key in keys:
            await self.delete(key)
            count += 1
        return count

    async def get_and_delete(self, key: str) -> CacheEntry | None:
        """Atomically retrieve and remove a cached entry.

        Use this for one-shot values (OAuth states, grants, invalidation) where
        exactly one of several concurrent callers may win: every other caller
        sees ``None``.

        The base implementation is a best-effort, NON-atomic get-then-delete
        fallback for third-party backends; the built-in backends override it
        with an atomic implementation.

        Returns:
            The entry that was stored under ``key``, or ``None`` if there was none
        """
        value = await self.get(key)
        if value is not None:
            await self.delete(key)
        return value

    async def set_if_absent(
        self, key: str, value: CacheEntry, ttl: int | None = None
    ) -> bool:
        """Store ``value`` only when ``key`` does not exist yet.

        The building block for locks and slots: of several concurrent callers
        exactly one stores its value and gets ``True``, every other caller
        gets ``False`` and the stored value is left untouched. An expired key
        counts as absent. Pair it with ``delete_if_equals`` to release only
        what you still hold.

        The base implementation is a best-effort, NON-atomic get-then-set
        fallback for third-party backends; the built-in backends override it
        with an atomic implementation.

        Args:
            key: Cache key to claim
            value: Entry to store, typically carrying a unique owner token
            ttl: Time to live in seconds (``None`` = never expires)

        Returns:
            Whether ``value`` was stored
        """
        validate_ttl(ttl)
        if await self.get(key) is not None:
            return False
        await self.set(key, value, ttl=ttl)
        return True

    async def delete_if_equals(self, key: str, expected: CacheEntry) -> bool:
        """Remove ``key`` only while it still holds ``expected``.

        Releasing a lock with a plain ``delete`` is unsafe: if the holder's
        entry expired and someone else claimed the key in the meantime, the
        delete removes the new holder's entry. Comparing against the value the
        caller stored makes the release a no-op in that case.

        The base implementation is a best-effort, NON-atomic get-compare-delete
        fallback for third-party backends; the built-in backends override it
        with an atomic implementation.

        Args:
            key: Cache key to release
            expected: The entry the caller stored (compared with ``==``)

        Returns:
            Whether the entry was removed
        """
        if await self.get(key) != expected:
            return False
        await self.delete(key)
        return True

    async def expire_if_equals(self, key: str, expected: CacheEntry, ttl: int) -> bool:
        """Update expiry on ``key`` to ``ttl`` seconds only while it still holds ``expected``.

        Re-setting a lock's TTL with a plain ``set`` is unsafe: if the holder's
        entry expired and someone else claimed the key in the meantime, a plain
        ``set`` overwrites the new holder's entry. Comparing against the value
        the caller stored makes the renewal a no-op in that case.

        The base implementation is a best-effort, NON-atomic get-compare-set
        fallback for third-party backends; the built-in backends override it
        with an atomic implementation.

        Args:
            key: Cache key to update expiry for
            expected: The entry the caller stored (compared with ``==``)
            ttl: Time to live in seconds

        Returns:
            Whether the expiry was updated
        """
        validate_ttl(ttl)
        if await self.get(key) != expected:
            return False
        await self.set(key, expected, ttl=ttl)
        return True

    async def increment(self, key: str, delta: int = 1, ttl: int | None = None) -> int:
        """Atomically add ``delta`` to the integer counter stored at ``key``.

        A missing key counts as 0: the first call creates the counter with the
        value ``delta`` and applies ``ttl`` (seconds; ``None`` = never expires).
        Later calls keep the existing expiry, so the counter lives in a fixed
        window that starts when it is created - the shape rate limiters need.
        The counter is readable through ``get()`` as a ``CacheEntry`` whose
        fingerprint is ``COUNTER_FINGERPRINT`` and whose content is the decimal
        value; ``delete``/``clear*`` treat it like any other entry.

        The base implementation is a best-effort, NON-atomic read-modify-write
        fallback for third-party backends and re-applies ``ttl`` on every call.
        The built-in backends override it with a single server-side operation.

        Args:
            key: Cache key of the counter
            delta: Amount to add (may be negative)
            ttl: Time to live in seconds, applied when the counter is created

        Returns:
            The counter value after the increment

        Raises:
            CacheXError: If ``key`` holds a cached response instead of a counter
            TypeError: If ``delta`` or ``ttl`` is not an ``int``
            ValueError: If ``ttl`` is out of range
        """
        validate_delta(delta)
        validate_ttl(ttl)
        current = await self.get(key)
        value = delta if current is None else counter_value(current) + delta
        await self.set(key, counter_entry(value), ttl=ttl)
        return value

    @abstractmethod
    async def clear(self) -> None:
        """Clear all cached responses."""

    @abstractmethod
    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path.

        Args:
            path: The path to clear cache for
            include_params: Whether to clear all parameter variations of the path

        Returns:
            Number of cache entries cleared
        """

    @abstractmethod
    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached entries whose key matches a glob pattern.

        The pattern is matched against the whole logical key — the key as the
        caller sees it, without whatever prefix the backend adds internally.
        HTTP cache keys are ``method|||host|||path|||query``, so matching a
        path means writing the other components out::

            await backend.clear_pattern("GET|||*|||/users/*")
            await backend.clear_pattern("cache:user:*")  # a CacheManager key

        To clear by path, prefer ``clear_path(path, include_params=...)``: it
        is built for exactly that and needs no knowledge of the key layout.
        Implementations report a path written here through
        ``warn_if_path_shaped`` rather than silently clearing nothing.

        Args:
            pattern: A glob pattern to match whole cache keys against

        Returns:
            Number of cache entries cleared
        """

    @abstractmethod
    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Returns:
            List of all cache keys currently stored in the backend
        """

    @abstractmethod
    async def get_cache_data(self) -> dict[str, tuple[Any, float | None]]:
        """Get all cache data with expiry information.

        This method is primarily used for cache monitoring and statistics.
        Returns cache keys mapped to tuples of (value, expiry_time).

        Returns:
            Dictionary mapping cache keys to (value, expiry) tuples.
            Expiry is None if the item never expires.
        """
