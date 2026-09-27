"""Generic application-level cache manager for FastAPI-CacheX."""

import fnmatch
import hashlib
import inspect
import json
import logging
import warnings
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from .backends.base import BaseCacheBackend
from .backends.base import validate_ttl
from .proxy import BackendProxy
from .types import CacheEntry
from .types import log_ref

logger = logging.getLogger(__name__)

_DECODE_ERRORS = (AttributeError, UnicodeDecodeError, json.JSONDecodeError)

# Characters that are live in a glob pattern: the Redis set, mirrored from
# ``backends/redis.py`` (fnmatch treats the backslash literally, but a prefix
# holding one still cannot be passed through to Redis unescaped).
_GLOB_SPECIAL = frozenset("*?[]\\")


class CacheManager:
    """Provides convenient get/set/delete access to the configured cache backend.

    Unlike the ``@cache`` decorator (which caches HTTP response bodies) or
    ``StateManager``/``SessionManager`` (which manage OAuth state and sessions),
    ``CacheManager`` is a thin, JSON-serializing wrapper for caching arbitrary
    application values under a dedicated key namespace.
    """

    def __init__(
        self,
        backend: BaseCacheBackend | None = None,
        key_prefix: str = "cache:",
        default_ttl: int | None = None,
    ) -> None:
        r"""Initialize CacheManager.

        Args:
            backend: Cache backend instance. If None, uses BackendProxy.get().
            key_prefix: Prefix prepended to all logical keys in the cache backend.
            default_ttl: Default TTL (seconds) applied when set() is called
                without an explicit ttl. None means no expiry by default.

        Raises:
            BackendNotFoundError: If ``backend`` is None and no backend has
                been set with ``BackendProxy.set()``.
            ValueError: If ``default_ttl`` is zero or negative.

        Warns:
            UserWarning: If ``key_prefix`` contains a glob metacharacter
                (``*``, ``?``, ``[``, ``]`` or ``\``). ``clear_pattern()`` then
                lists every key and filters in Python instead of handing the
                pattern to the backend.
        """
        self.backend = backend if backend is not None else BackendProxy.get()
        self.key_prefix = key_prefix
        self.default_ttl = validate_ttl(default_ttl)
        if self._prefix_has_glob:
            warnings.warn(
                f"CacheManager key_prefix {key_prefix!r} contains a glob "
                "metacharacter, so clear_pattern() will list every key in the "
                "backend and filter them in Python, which is slower on Redis "
                "than a server-side SCAN MATCH. Use a prefix without any of "
                "*?[]\\ to keep the fast path.",
                UserWarning,
                stacklevel=2,
            )

    @property
    def _prefix_has_glob(self) -> bool:
        return not _GLOB_SPECIAL.isdisjoint(self.key_prefix)

    def _cache_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"

    @staticmethod
    def _encode(value: Any) -> CacheEntry:
        content = json.dumps(value).encode("utf-8")
        fingerprint = hashlib.sha256(content).hexdigest()
        return CacheEntry(fingerprint=fingerprint, content=content)

    async def get(self, key: str, default: Any = None) -> Any:
        """Retrieve and JSON-decode a cached value.

        Args:
            key: Logical cache key (without the manager's prefix).
            default: Value returned when the key is missing, expired, or the
                stored content cannot be decoded.

        Returns:
            The cached value, or ``default`` on a miss or decode failure.
        """
        cached = await self.backend.get(self._cache_key(key))
        if cached is None:
            return default

        try:
            return json.loads(cached.content)
        except _DECODE_ERRORS:
            # Keys often embed user IDs or e-mails: only a digest at WARNING.
            logger.warning("Failed to decode cached value; key_ref=%s", log_ref(key))
            logger.debug("Failed to decode cached value; key=%s", key)
            return default

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """JSON-encode and store a value in the cache.

        Args:
            key: Logical cache key (without the manager's prefix).
            value: A JSON-serializable Python value.
            ttl: Time-to-live in seconds. If None, uses ``self.default_ttl``
                (which itself defaults to no expiry).

        Raises:
            TypeError: If ``value`` is not JSON-serializable.
            ValueError: If ``ttl`` is zero or negative.
        """
        effective_ttl = validate_ttl(ttl if ttl is not None else self.default_ttl)
        entry = self._encode(value)

        await self.backend.set(self._cache_key(key), entry, ttl=effective_ttl)
        logger.debug("Cache SET; key=%s ttl=%s", key, effective_ttl)

    async def add(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Store a value only if the key is not already in the cache.

        The check and the write are one atomic backend operation
        (``set_if_absent``), so of several concurrent callers adding the same
        key exactly one gets ``True``. Use it to do something once per key,
        such as sending a webhook or recording a first occurrence.

        An expired key counts as absent. A key holding a value that cannot be
        decoded still exists, so ``add()`` returns ``False`` for it, whereas
        ``get()`` and ``get_or_set()`` treat it as a miss.

        Args:
            key: Logical cache key (without the manager's prefix).
            value: A JSON-serializable Python value.
            ttl: Time-to-live in seconds. If None, uses ``self.default_ttl``
                (which itself defaults to no expiry).

        Returns:
            True if the value was stored, False if the key already existed.

        Raises:
            TypeError: If ``value`` is not JSON-serializable.
            ValueError: If ``ttl`` is zero or negative.
        """
        effective_ttl = validate_ttl(ttl if ttl is not None else self.default_ttl)
        entry = self._encode(value)

        added = await self.backend.set_if_absent(
            self._cache_key(key), entry, ttl=effective_ttl
        )
        logger.debug("Cache ADD; key=%s ttl=%s added=%s", key, effective_ttl, added)
        return added

    async def delete(self, key: str) -> bool:
        """Remove a value from the cache.

        Args:
            key: Logical cache key (without the manager's prefix).

        Returns:
            True if the key existed and was deleted, False otherwise.
        """
        if await self.backend.get_and_delete(self._cache_key(key)) is None:
            return False
        logger.debug("Cache DELETE; key=%s", key)
        return True

    async def has(self, key: str) -> bool:
        """Check whether a key exists in the cache without decoding its value.

        Args:
            key: Logical cache key (without the manager's prefix).

        Returns:
            True if the key exists and has not expired.
        """
        return await self.backend.get(self._cache_key(key)) is not None

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl: int | None = None,
    ) -> Any:
        """Get a cached value, computing and storing it via ``factory`` on a miss.

        ``factory`` is only invoked when ``key`` is missing, expired, or its
        stored content cannot be decoded; on a hit the cached value is
        returned directly. This method does not provide stampede protection:
        concurrent misses for the same key may each invoke ``factory``.

        A miss returns the value as it will be read back from the cache, not
        the object ``factory`` returned: it goes through the same JSON
        round-trip as a hit, so a tuple comes back as a list and integer
        dict keys as strings. A value JSON cannot encode (``datetime``,
        ``Decimal``, ``UUID``, a pydantic model) raises ``TypeError`` only
        after ``factory`` has run; nothing is stored.

        Args:
            key: Logical cache key (without the manager's prefix).
            factory: Zero-argument callable that produces the JSON-serializable
                value to cache on a miss. If it returns an awaitable (an async
                function, or a lambda or ``functools.partial`` wrapping one),
                the result is awaited.
            ttl: Time-to-live in seconds for a newly created value. If None,
                uses ``self.default_ttl``.

        Returns:
            The cached value (existing or newly created), JSON-decoded in
            both cases.

        Raises:
            TypeError: If the value produced by ``factory`` is not JSON-serializable.
            ValueError: If ``ttl`` is zero or negative.
        """
        # Reject a bad ttl before the factory does any (possibly costly) work.
        validate_ttl(ttl)
        sentinel = object()
        cached = await self.get(key, default=sentinel)
        if cached is not sentinel:
            return cached

        # Await whatever comes back awaitable, not just from coroutine
        # functions: `lambda: load(42)` and `functools.partial` return one too.
        value = factory()
        if inspect.isawaitable(value):
            value = await value

        # Encode once, store those bytes and return them decoded, so a miss
        # returns exactly what a later hit will: a tuple comes back as a
        # list, int dict keys as strings.
        effective_ttl = ttl if ttl is not None else self.default_ttl
        entry = self._encode(value)
        await self.backend.set(self._cache_key(key), entry, ttl=effective_ttl)
        logger.debug("Cache SET; key=%s ttl=%s", key, effective_ttl)
        return json.loads(entry.content)

    async def clear_pattern(self, pattern: str) -> int:
        r"""Clear all keys under this manager's namespace matching a glob pattern.

        Only ``pattern`` is a glob; ``self.key_prefix`` is always matched
        literally.

        When ``key_prefix`` holds no glob metacharacter (``*?[]\``), this
        delegates to the backend's native ``clear_pattern`` (e.g. Redis
        ``SCAN MATCH``), and ``pattern`` uses the backend's glob syntax.

        Otherwise it cannot pass the prefix to the backend as a glob, so it
        lists every key with ``get_all_keys()``, keeps those that start with
        ``key_prefix`` and whose remainder matches ``pattern`` under
        ``fnmatch.fnmatchcase``, and removes them with ``delete_many()``. That
        is slower on Redis, and ``pattern`` is then fnmatch syntax rather than
        Redis glob: no backslash escapes, and ``[!a]`` rather than ``[^a]``
        for negation. The constructor warns about such a prefix.

        Backends without key enumeration (e.g. Memcached) cannot honor either
        path and return 0 with a ``RuntimeWarning``.

        Args:
            pattern: Glob pattern (relative to ``self.key_prefix``) to match
                against, e.g. ``"user:*"``.

        Returns:
            Number of cache entries cleared.
        """
        match_pattern = self._cache_key(pattern)
        if self._prefix_has_glob:
            prefix = self.key_prefix
            keys = await self.backend.get_all_keys()
            cleared = await self.backend.delete_many(
                key
                for key in keys
                if key.startswith(prefix)
                and fnmatch.fnmatchcase(key.removeprefix(prefix), pattern)
            )
        else:
            cleared = await self.backend.clear_pattern(match_pattern)
        logger.debug(
            "Cache CLEAR_PATTERN; pattern=%s removed=%s", match_pattern, cleared
        )
        return cleared

    async def clear_prefix(self, prefix: str | None = None) -> int:
        """Clear all keys under this manager's namespace matching a sub-prefix.

        Args:
            prefix: Optional additional prefix (relative to ``self.key_prefix``)
                to restrict which keys are cleared. If None, clears everything
                under ``self.key_prefix``.

        Returns:
            Number of cache entries cleared.
        """
        match_prefix = self._cache_key(prefix or "")
        keys = await self.backend.get_all_keys()
        removed = await self.backend.delete_many(
            key for key in keys if key.startswith(match_prefix)
        )
        logger.debug("Cache CLEAR_PREFIX; prefix=%s removed=%s", match_prefix, removed)
        return removed

    async def clear(self) -> int:
        """Clear all keys under this manager's namespace.

        Returns:
            Number of cache entries cleared.
        """
        return await self.clear_prefix()
