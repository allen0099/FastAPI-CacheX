"""Generic application-level cache manager for FastAPI-CacheX."""

import asyncio
import contextvars
import fnmatch
import hashlib
import inspect
import json
import logging
import secrets
import time
import warnings
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from .backends.base import BaseCacheBackend
from .backends.base import validate_ttl
from .exceptions import LockTimeoutError
from .lock import CacheLock
from .proxy import BackendProxy
from .types import CacheEntry
from .types import log_ref

logger = logging.getLogger(__name__)

_DECODE_ERRORS = (AttributeError, UnicodeDecodeError, json.JSONDecodeError)

# Characters that are live in a glob pattern: the Redis set, mirrored from
# ``backends/redis.py`` (fnmatch treats the backslash literally, but a prefix
# holding one still cannot be passed through to Redis unescaped).
_GLOB_SPECIAL = frozenset("*?[]\\")

# Keys currently held under a stampede-protection lock by the running task.
# Stored as a set of unique backend-and-key identifiers to skip locking on
# re-entrancy within the same task.
_HELD_LOCKS: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "_HELD_LOCKS", default=frozenset()
)

# Polling configuration for stampede protection waiters
_INITIAL_POLL_INTERVAL: float = 0.05
_MAX_POLL_INTERVAL: float = 0.5
_BACKOFF_FACTOR: float = 1.5
_JITTER_RATIO: float = 0.1

_system_random = secrets.SystemRandom()
_SENTINEL = object()

# Hooks for tests to inject artificial clocks and sleeping
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
_monotonic: Callable[[], float] = time.monotonic


def _validate_lock(lock: Any, *, allow_none: bool = False) -> None:
    if lock is None:
        if allow_none:
            return
        msg = "lock must be a bool, got None"
        raise TypeError(msg)
    if not isinstance(lock, bool):
        expected = "a bool or None" if allow_none else "a bool"
        msg = f"lock must be {expected}, got {type(lock).__name__}"
        raise TypeError(msg)


def _validate_get_or_set_args(
    lock: bool | None,
    ttl: int | None,
    lock_ttl: int | None,
    wait_timeout: float | None,
) -> float | None:
    _validate_lock(lock, allow_none=True)
    validate_ttl(ttl)
    if lock_ttl is not None:
        validate_ttl(lock_ttl)
    if wait_timeout is not None:
        if isinstance(wait_timeout, bool) or not isinstance(wait_timeout, (int, float)):
            msg = (
                f"wait_timeout must be a number of seconds or None, "
                f"got {type(wait_timeout).__name__}"
            )
            raise TypeError(msg)
        if wait_timeout <= 0:
            msg = f"wait_timeout must be greater than zero, got {wait_timeout!r}"
            raise ValueError(msg)
        return float(wait_timeout)
    return None


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
        *,
        lock: bool = True,
        lock_ttl: int = 60,
    ) -> None:
        r"""Initialize CacheManager.

        Args:
            backend: Cache backend instance. If None, uses BackendProxy.get().
            key_prefix: Prefix prepended to all logical keys in the cache backend.
            default_ttl: Default TTL (seconds) applied when set() is called
                without an explicit ttl. None means no expiry by default.
            lock: Whether get_or_set() uses distributed locking by default to
                prevent cache stampedes (default: True). Pass ``False`` to
                compute on every concurrent miss without the extra backend
                round trips.
            lock_ttl: Default TTL in seconds for stampede protection locks (default: 60).

        Raises:
            BackendNotFoundError: If ``backend`` is None and no backend has
                been set with ``BackendProxy.set()``.
            TypeError: If ``lock`` is not a bool, or ``default_ttl``
                or ``lock_ttl`` is not an int.
            ValueError: If ``default_ttl`` or ``lock_ttl`` is zero, negative
                or larger than ``MAX_TTL``, or ``lock_ttl`` is None.

        Warns:
            UserWarning: If ``key_prefix`` contains a glob metacharacter
                (``*``, ``?``, ``[``, ``]`` or ``\``). ``clear_pattern()`` then
                lists every key and filters in Python instead of handing the
                pattern to the backend.
        """
        self.backend = backend if backend is not None else BackendProxy.get()
        self.key_prefix = key_prefix
        self.default_ttl = validate_ttl(default_ttl)

        _validate_lock(lock)

        effective_lock_ttl = validate_ttl(lock_ttl)
        if effective_lock_ttl is None:
            msg = "lock_ttl must be a positive int, got None"
            raise ValueError(msg)
        self.lock: bool = lock
        self.lock_ttl: int = effective_lock_ttl

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
            TypeError: If ``value`` is not JSON-serializable, or ``ttl`` is
                not an int.
            ValueError: If ``ttl`` is zero, negative or larger than ``MAX_TTL``.
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
            TypeError: If ``value`` is not JSON-serializable, or ``ttl`` is
                not an int.
            ValueError: If ``ttl`` is zero, negative or larger than ``MAX_TTL``.
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

    async def _compute_and_store(
        self,
        key: str,
        factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl: int | None,
    ) -> Any:
        value = factory()
        if inspect.isawaitable(value):
            value = await value

        effective_ttl = ttl if ttl is not None else self.default_ttl
        entry = self._encode(value)
        await self.backend.set(self._cache_key(key), entry, ttl=effective_ttl)
        logger.debug("Cache SET; key=%s ttl=%s", key, effective_ttl)
        return json.loads(entry.content)

    async def _execute_as_winner(
        self,
        key: str,
        factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl: int | None,
        lock_instance: CacheLock,
    ) -> Any:
        lock_id = f"{id(self.backend)}:{self._cache_key(key)}"
        token = _HELD_LOCKS.set(_HELD_LOCKS.get() | {lock_id})
        try:
            cached = await self.get(key, default=_SENTINEL)
            if cached is not _SENTINEL:
                return cached
            return await self._compute_and_store(key, factory, ttl)
        finally:
            try:
                try:
                    await lock_instance.release()
                except Exception:
                    logger.warning(
                        "Failed to release stampede protection lock for key_ref=%s",
                        log_ref(key),
                        exc_info=True,
                    )
            finally:
                _HELD_LOCKS.reset(token)

    async def _poll_for_value(  # noqa: PLR0913
        self,
        key: str,
        factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl: int | None,
        lock_ttl: int,
        wait_timeout: float | None,
        *,
        raise_on_timeout: bool,
    ) -> Any:
        start = _monotonic()
        interval = _INITIAL_POLL_INTERVAL
        while True:
            if wait_timeout is not None:
                elapsed = _monotonic() - start
                if elapsed >= wait_timeout:
                    cached = await self.get(key, default=_SENTINEL)
                    if cached is not _SENTINEL:
                        return cached
                    if raise_on_timeout:
                        msg = f"Waiting for cache key {key!r} timed out after {wait_timeout}s"
                        raise LockTimeoutError(msg)
                    logger.warning(
                        "Cache stampede wait timeout exceeded for key_ref=%s; computing directly",
                        log_ref(key),
                    )
                    return await self._compute_and_store(key, factory, ttl)

            jitter = _system_random.uniform(
                -interval * _JITTER_RATIO, interval * _JITTER_RATIO
            )
            sleep_time = max(0.0, interval + jitter)
            if wait_timeout is not None:
                remaining = wait_timeout - elapsed
                sleep_time = min(sleep_time, remaining)
            await _sleep(sleep_time)

            interval = min(interval * _BACKOFF_FACTOR, _MAX_POLL_INTERVAL)

            # 1. Check cache, return on hit
            cached = await self.get(key, default=_SENTINEL)
            if cached is not _SENTINEL:
                return cached

            # 2. Otherwise try to take lock
            lock_instance = CacheLock(
                name=self._cache_key(key),
                ttl=lock_ttl,
                backend=self.backend,
            )
            if await lock_instance.acquire(blocking=False):
                return await self._execute_as_winner(key, factory, ttl, lock_instance)

    async def get_or_set(  # noqa: PLR0913
        self,
        key: str,
        factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl: int | None = None,
        *,
        lock: bool | None = None,
        lock_ttl: int | None = None,
        wait_timeout: float | None = None,
        raise_on_timeout: bool = False,
    ) -> Any:
        """Get a cached value, computing and storing it via ``factory`` on a miss.

        ``factory`` is only invoked when ``key`` is missing, expired, or its
        stored content cannot be decoded; on a hit the cached value is
        returned directly.

        When stampede protection is enabled (via ``lock=True`` or the
        manager's ``lock`` default), concurrent misses for the same key acquire
        a distributed lock built on :class:`~fastapi_cachex.lock.CacheLock`.
        The winner re-checks the cache, invokes ``factory``, stores the result,
        and releases the lock. Waiting callers poll the cache with exponential
        backoff and jitter until the value appears, taking over the lock if the
        winner fails or the lock lease expires.

        Re-entrant calls to ``get_or_set()`` for the same key within the
        current task skip locking to prevent self-deadlock.

        Ensure ``lock_ttl`` exceeds the expected execution time of ``factory``.
        If ``factory`` outlives ``lock_ttl``, the lock expires mid-run and a
        waiting caller may start a second computation.

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
            lock: Whether to use distributed locking for stampede protection.
                If None, inherits the manager's ``lock`` setting.
            lock_ttl: Upper bound in seconds for the lock lease. If None,
                inherits the manager's ``lock_ttl``.
            wait_timeout: Maximum seconds waiting callers poll the cache before
                timing out. If None, callers wait without a fixed deadline,
                bounded by the holder's lock lease, and attempt to take over the
                lock if it expires.
            raise_on_timeout: If True, raise :exc:`~fastapi_cachex.exceptions.LockTimeoutError`
                when ``wait_timeout`` elapses. If False (default), log a warning and
                fall back to invoking ``factory`` directly.

        Returns:
            The cached value (existing or newly created), JSON-decoded in
            both cases.

        Raises:
            TypeError: If the value produced by ``factory`` is not JSON-serializable,
                or if ``lock``, ``ttl``, ``lock_ttl``, or ``wait_timeout``
                have invalid types.
            ValueError: If ``ttl``, ``lock_ttl``, or ``wait_timeout`` is zero or
                negative, or ``ttl`` or ``lock_ttl`` is larger than ``MAX_TTL``.
            LockTimeoutError: If ``raise_on_timeout=True`` and waiting exceeds ``wait_timeout``.
        """
        validated_wait_timeout = _validate_get_or_set_args(
            lock, ttl, lock_ttl, wait_timeout
        )
        cached = await self.get(key, default=_SENTINEL)
        if cached is not _SENTINEL:
            return cached

        use_lock = self.lock if lock is None else lock
        if not use_lock:
            return await self._compute_and_store(key, factory, ttl)

        lock_id = f"{id(self.backend)}:{self._cache_key(key)}"
        if lock_id in _HELD_LOCKS.get():
            logger.debug("Re-entrant get_or_set call for key=%s; skipping lock", key)
            return await self._compute_and_store(key, factory, ttl)

        effective_lock_ttl = self.lock_ttl if lock_ttl is None else lock_ttl
        lock_instance = CacheLock(
            name=self._cache_key(key),
            ttl=effective_lock_ttl,
            backend=self.backend,
        )
        if await lock_instance.acquire(blocking=False):
            return await self._execute_as_winner(key, factory, ttl, lock_instance)

        return await self._poll_for_value(
            key,
            factory,
            ttl,
            effective_lock_ttl,
            validated_wait_timeout,
            raise_on_timeout=raise_on_timeout,
        )

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

        Built on ``get_all_keys()`` and ``delete_many()``, so on a backend
        without key enumeration (e.g. Memcached) it clears nothing and returns
        0, with a ``RuntimeWarning``.

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

        Same as ``clear_prefix()`` with no prefix, so it is a no-op on a
        backend without key enumeration (e.g. Memcached).

        Returns:
            Number of cache entries cleared.
        """
        return await self.clear_prefix()
