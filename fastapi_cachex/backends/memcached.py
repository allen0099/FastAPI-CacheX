"""Memcached cache backend implementation."""

import asyncio
import hashlib
import logging
import time
import warnings

from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.backends.codec import encode_entry
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.types import CacheEntry

from .base import BaseCacheBackend
from .base import validate_ttl

logger = logging.getLogger(__name__)

# Default Memcached key prefix for fastapi-cachex
DEFAULT_MEMCACHE_PREFIX = "fastapi_cachex:"

# Memcached accepts keys of at most 250 bytes, and pymemcache rejects any key
# containing whitespace, control characters or non-ASCII bytes. What is left is
# the printable ASCII range with the space removed.
_MAX_KEY_BYTES = 250
_LEGAL_KEY_BYTES = frozenset(range(0x21, 0x7F))

# An exptime above 30 days is read by Memcached as an absolute Unix timestamp,
# not as a duration, so a longer TTL has to be converted before it is sent.
_MAX_RELATIVE_TTL = 30 * 24 * 60 * 60


def _expiry(ttl: int | None) -> int:
    """Convert a TTL in seconds to the exptime Memcached expects.

    Anything past the 30-day boundary is sent as an absolute timestamp;
    passing it through as a duration would have Memcached read it as a moment
    in 1970 and expire the entry immediately. ``None`` means no expiry.
    """
    if ttl is None:
        return 0
    if ttl > _MAX_RELATIVE_TTL:
        return int(time.time()) + ttl
    return ttl


class MemcachedBackend(BaseCacheBackend):
    """Memcached backend implementation.

    Note: This implementation uses the synchronous pymemcache client and runs
    each call in a worker thread. The client is connection-pooled so concurrent
    calls never share a socket. For true async Memcached operations consider
    aiomcache. Keys are namespaced with 'fastapi_cachex:' by default to avoid
    conflicts with other applications.

    Limitations:
    - Pattern-based clearing (clear_pattern) is not supported by Memcached protocol
    - Operations are wrapped to appear async but use blocking sync client internally
    """

    key_prefix: str

    def __init__(
        self,
        servers: list[str],
        key_prefix: str = DEFAULT_MEMCACHE_PREFIX,
    ) -> None:
        """Initialize the Memcached backend.

        Args:
            servers: List of Memcached servers in format ["host:port", ...]
            key_prefix: Prefix for all cache keys (default: 'fastapi_cachex:')

        Raises:
            CacheXError: If pymemcache is not installed
        """
        try:
            from pymemcache import HashClient
        except ImportError:
            msg = "pymemcache is not installed. Please install it with 'pip install pymemcache'"
            raise CacheXError(msg)

        # Pooled connections have no ordering guarantee between each other, so
        # every write waits for the server's acknowledgement; otherwise a
        # ``set`` on one socket may still be in flight when a ``get`` on another
        # socket is served, and the caller would miss its own write.
        self.client = HashClient(
            servers,
            connect_timeout=5,
            timeout=5,
            use_pooling=True,
            default_noreply=False,
        )
        self.key_prefix = key_prefix

    def _make_key(self, key: str) -> str:
        """Namespace a cache key, hashing it when Memcached would refuse it.

        pymemcache raises ``MemcacheIllegalInputError`` for a key over 250
        bytes or carrying whitespace, control characters or non-ASCII bytes,
        and nothing catches it on the way out — so ordinary traffic could turn
        into a 500. ASGI percent-decodes the path, so `/foo%20bar` alone builds
        a key with a literal space in it, and a long query string easily runs
        past 250 bytes.

        A key Memcached would accept is returned byte-for-byte, which keeps
        entries written by earlier versions readable; only the rest collapse to
        a SHA-256 digest of the whole namespaced key.
        """
        prefixed = f"{self.key_prefix}{key}"
        encoded = prefixed.encode("utf-8")
        if len(encoded) <= _MAX_KEY_BYTES and _LEGAL_KEY_BYTES.issuperset(encoded):
            return prefixed
        digest = hashlib.sha256(encoded).hexdigest()
        logger.debug("Memcached key hashed; key=%s digest=%s", key, digest)
        return f"{self.key_prefix}{digest}"

    async def get(self, key: str) -> CacheEntry | None:
        """Get value from cache.

        Args:
            key: Cache key to retrieve

        Returns:
            Cached entry if found, None otherwise
        """
        raw = await asyncio.to_thread(self.client.get, self._make_key(key))
        value = decode_entry(raw)
        if raw is None:
            logger.debug("Memcached MISS; key=%s", key)
        elif value is None:
            logger.debug("Memcached DESERIALIZE ERROR; key=%s", key)
        else:
            logger.debug("Memcached HIT; key=%s", key)
        return value

    async def set(self, key: str, value: CacheEntry, ttl: int | None = None) -> None:
        """Set value in cache.

        Args:
            key: Cache key
            value: CacheEntry instance to store
            ttl: Time to live in seconds
        """
        validate_ttl(ttl)
        await asyncio.to_thread(
            self.client.set, self._make_key(key), encode_entry(value), _expiry(ttl)
        )
        logger.debug("Memcached SET; key=%s ttl=%s", key, ttl)

    async def get_and_delete(self, key: str) -> CacheEntry | None:
        """Atomically retrieve and remove a cached entry (see base class).

        Memcached has no combined primitive, but DELETE is atomic: the value is
        returned only when this call is the one that removed it, so exactly one
        concurrent caller wins.
        """
        prefixed_key = self._make_key(key)
        raw = await asyncio.to_thread(self.client.get, prefixed_key)
        if raw is None:
            logger.debug("Memcached GET_AND_DELETE MISS; key=%s", key)
            return None
        deleted = await asyncio.to_thread(
            self.client.delete, prefixed_key, noreply=False
        )
        if not deleted:
            logger.debug("Memcached GET_AND_DELETE LOST RACE; key=%s", key)
            return None
        logger.debug("Memcached GET_AND_DELETE HIT; key=%s", key)
        return decode_entry(raw)

    async def set_if_absent(
        self, key: str, value: CacheEntry, ttl: int | None = None
    ) -> bool:
        """Atomically store ``value`` unless ``key`` exists (see base class).

        Memcached's ``ADD`` is exactly this operation.
        """
        validate_ttl(ttl)
        stored = await asyncio.to_thread(
            self.client.add,
            self._make_key(key),
            encode_entry(value),
            _expiry(ttl),
            noreply=False,
        )
        logger.debug(
            "Memcached SET_IF_ABSENT %s; key=%s ttl=%s",
            "STORED" if stored else "EXISTS",
            key,
            ttl,
        )
        return bool(stored)

    async def delete_if_equals(self, key: str, expected: CacheEntry) -> bool:
        """Atomically remove ``key`` while it holds ``expected`` (see base class).

        The classic protocol's DELETE takes no CAS token, so the release is a
        CAS write with a negative exptime, which Memcached treats as "expired
        immediately": it succeeds only if nothing wrote the key since ``GETS``
        read the value that was compared.
        """
        prefixed_key = self._make_key(key)
        raw, cas_token = await asyncio.to_thread(self.client.gets, prefixed_key)
        if raw is None or decode_entry(raw) != expected:
            logger.debug("Memcached DELETE_IF_EQUALS MISMATCH; key=%s", key)
            return False
        deleted = await asyncio.to_thread(
            self.client.cas, prefixed_key, b"", cas_token, -1, noreply=False
        )
        logger.debug(
            "Memcached DELETE_IF_EQUALS %s; key=%s",
            "HIT" if deleted else "LOST RACE",
            key,
        )
        return bool(deleted)

    async def expire_if_equals(self, key: str, expected: CacheEntry, ttl: int) -> bool:
        """Atomically update expiry on ``key`` while it holds ``expected`` (see base class).

        TOUCH in the Memcached protocol takes no CAS token, so the renewal is a
        CAS write with the same bytes read by GETS and the new exptime.
        """
        validate_ttl(ttl)
        prefixed_key = self._make_key(key)
        raw, cas_token = await asyncio.to_thread(self.client.gets, prefixed_key)
        if raw is None or decode_entry(raw) != expected:
            logger.debug("Memcached EXPIRE_IF_EQUALS MISMATCH; key=%s", key)
            return False
        updated = await asyncio.to_thread(
            self.client.cas, prefixed_key, raw, cas_token, _expiry(ttl), noreply=False
        )
        logger.debug(
            "Memcached EXPIRE_IF_EQUALS %s; key=%s ttl=%s",
            "HIT" if updated else "LOST RACE",
            key,
            ttl,
        )
        return bool(updated)

    def _add_delta(self, prefixed_key: str, delta: int) -> int | None:
        """Apply ``delta`` with INCR/DECR; ``None`` when the key does not exist."""
        if delta < 0:
            result = self.client.decr(prefixed_key, -delta, noreply=False)
        else:
            result = self.client.incr(prefixed_key, delta, noreply=False)
        return None if result is None else int(result)

    async def increment(self, key: str, delta: int = 1, ttl: int | None = None) -> int:
        """Atomically add ``delta`` to the counter at ``key`` (see base class).

        Memcached counters are unsigned, so a negative ``delta`` uses DECR,
        which stops at 0 instead of going negative.
        """
        validate_ttl(ttl)
        from pymemcache.exceptions import MemcacheClientError

        prefixed_key = self._make_key(key)
        try:
            value = await asyncio.to_thread(self._add_delta, prefixed_key, delta)
            if value is None:
                # No counter yet: ADD is atomic and a no-op when a concurrent
                # call created it first, so the retry always finds a counter.
                await asyncio.to_thread(
                    self.client.add, prefixed_key, b"0", _expiry(ttl), noreply=False
                )
                value = await asyncio.to_thread(self._add_delta, prefixed_key, delta)
        except MemcacheClientError as e:
            msg = "Cache key holds a value that is not a counter"
            raise CacheXError(msg) from e
        if value is None:
            msg = "Counter vanished between ADD and INCR"
            raise CacheXError(msg)
        logger.debug("Memcached INCREMENT; key=%s value=%s ttl=%s", key, value, ttl)
        return value

    async def delete(self, key: str) -> None:
        """Delete value from cache.

        Args:
            key: Cache key to delete
        """
        prefixed = self._make_key(key)
        await asyncio.to_thread(self.client.delete, prefixed)
        logger.debug("Memcached DELETE; key=%s", key)

    async def clear(self) -> None:
        """Clear all values from cache.

        Note: Memcached's flush_all affects the entire server, including
        other applications' keys. Memcached cannot enumerate keys, so there
        is no way to clear only this namespace; delete keys you know by name
        with ``delete()``/``delete_many()`` instead.
        """
        warnings.warn(
            "Memcached.clear() flushes ALL cached data from the server, "
            "affecting other applications. Memcached cannot enumerate keys, so "
            "this namespace cannot be cleared on its own; delete known keys "
            "with delete() or delete_many() instead.",
            RuntimeWarning,
            stacklevel=2,
        )
        await asyncio.to_thread(self.client.flush_all)
        logger.debug("Memcached CLEAR; flush_all issued")

    async def clear_path(self, path: str, include_params: bool = False) -> int:
        """Clear cached responses for a specific path.

        Note: Memcached does not support pattern-based queries, so this
        only deletes the key that is exactly ``path``. HTTP route keys
        (``method|||host|||path|||query``) are not matched. For path-based
        clearing, use the Redis or memory backend.

        Args:
            path: The exact key to delete
            include_params: Unsupported; emits a ``RuntimeWarning`` and is
                otherwise ignored

        Returns:
            Number of cache entries cleared (0 or 1 for exact match only)
        """
        if include_params:
            warnings.warn(
                "Memcached backend does not support pattern-based key clearing. "
                "Only exact key matches can be deleted. "
                "The include_params option has no effect. "
                "Consider using Redis backend for pattern support.",
                RuntimeWarning,
                stacklevel=2,
            )

        # Try to delete the prefixed key (exact match only)
        prefixed_key = self._make_key(path)
        result = await asyncio.to_thread(
            self.client.delete, prefixed_key, noreply=False
        )
        logger.debug(
            "Memcached CLEAR_PATH; path=%s include_params=%s removed=%s",
            path,
            include_params,
            1 if result else 0,
        )
        return 1 if result else 0

    async def clear_pattern(self, pattern: str) -> int:
        """Clear cached responses matching a pattern.

        Memcached does not support pattern matching or key scanning.
        This operation is not available.

        Args:
            pattern: A glob pattern (not supported by Memcached)

        Returns:
            Always 0, as pattern matching is not supported
        """
        warnings.warn(
            "Memcached backend does not support pattern matching. "
            "Pattern-based cache clearing is not available with Memcached. "
            "Consider using Redis backend for pattern support, "
            "or track keys manually in your application logic.",
            RuntimeWarning,
            stacklevel=2,
        )
        logger.debug("Memcached CLEAR_PATTERN unsupported; pattern=%s", pattern)
        return 0

    async def get_all_keys(self) -> list[str]:
        """Get all cache keys in the backend.

        Note: Memcached does not support key scanning directly.
        This returns an empty list as Memcached has no built-in way to enumerate keys.
        For key enumeration, consider using Redis backend or tracking keys
        manually in your application.

        Returns:
            Empty list (Memcached limitation)
        """
        warnings.warn(
            "Memcached backend does not support key enumeration. "
            "get_all_keys() returns an empty list. "
            "Consider using Redis backend if you need cache monitoring, "
            "or track keys manually in your application.",
            RuntimeWarning,
            stacklevel=2,
        )
        logger.debug("Memcached GET_ALL_KEYS unsupported; returning empty list")
        return []

    async def get_cache_data(self) -> dict[str, tuple[CacheEntry, float | None]]:
        """Get all cache data with expiry information.

        Note: Memcached does not support key enumeration or pattern matching.
        This method returns an empty dictionary.

        Returns:
            Empty dictionary (Memcached limitation)
        """
        warnings.warn(
            "Memcached backend does not support key enumeration. "
            "get_cache_data() returns an empty dictionary. "
            "Consider using Redis backend if you need cache monitoring.",
            RuntimeWarning,
            stacklevel=2,
        )
        logger.debug("Memcached GET_CACHE_DATA unsupported; returning empty dict")
        return {}
