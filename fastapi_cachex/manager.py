"""Generic application-level cache manager for FastAPI-CacheX."""

import asyncio
import contextvars
import dataclasses
import fnmatch
import functools
import hashlib
import inspect
import json
import logging
import secrets
import time
import warnings
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from typing import TypeVar
from typing import overload

from pydantic import BaseModel
from pydantic import SecretBytes
from pydantic import SecretStr
from pydantic import TypeAdapter
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from pydantic_core import to_jsonable_python

from .backends.base import BaseCacheBackend
from .backends.base import validate_ttl
from .cache_key import GLOB_SPECIAL
from .exceptions import LockTimeoutError
from .lock import CacheLock
from .proxy import BackendProxy
from .types import CacheEntry
from .types import log_ref

logger = logging.getLogger(__name__)

_DECODE_ERRORS = (AttributeError, UnicodeDecodeError, json.JSONDecodeError)

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

T = TypeVar("T")
D = TypeVar("D")


_NOT_STORED = (Iterator, bytes, bytearray, memoryview, SecretStr, SecretBytes)


def _holds_a_secret(value: Any) -> bool:
    """Whether a model or dataclass has a secret field, at any depth.

    Dumped in python mode, where the secret objects survive (the JSON form
    has already masked them). An excluded field is not in the dump, and is
    not stored either.
    """
    if isinstance(value, BaseModel) or (
        dataclasses.is_dataclass(value) and not isinstance(value, type)
    ):
        value = _adapter(type(value)).dump_python(value)
    if isinstance(value, (SecretStr, SecretBytes)):
        return True
    if isinstance(value, dict):
        return any(_holds_a_secret(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_holds_a_secret(item) for item in value)
    return False


def _jsonable(value: Any) -> Any:
    """``json.dumps`` fallback: what pydantic would write for ``value``.

    Models, dataclasses, ``datetime``, ``Decimal``, ``UUID``, sets and the
    like. A value pydantic does not know either, or one it would not write
    faithfully, stays a ``TypeError``, as ``json.dumps`` raises on its own.
    """
    msg = f"Object of type {type(value).__name__} is not JSON serializable"
    # pydantic would write each of these, and none of them survives it: an
    # iterator (a generator, an open file) is consumed, bytes come back as
    # text, and a secret is stored as its mask.
    if isinstance(value, _NOT_STORED):
        raise TypeError(msg)
    if _holds_a_secret(value):
        # Stored as "**********", it would be read back, and validated by
        # type_=, as if that were the secret.
        msg = (
            f"Object of type {type(value).__name__} is not JSON serializable: "
            "it has a SecretStr or SecretBytes field, which would be stored as "
            "its mask. Exclude the field (model_dump(exclude=...)) or cache a "
            "dict you build yourself"
        )
        raise TypeError(msg)
    try:
        return to_jsonable_python(value)
    except (PydanticSerializationError, ValueError) as e:
        raise TypeError(msg) from e


@functools.lru_cache(maxsize=256)
def _cached_adapter(type_: Any) -> TypeAdapter[Any]:
    return TypeAdapter(type_)


def _adapter(type_: Any) -> TypeAdapter[Any]:
    """The ``TypeAdapter`` for ``type_``; building one is slow, so they are kept."""
    try:
        return _cached_adapter(type_)
    except TypeError:  # an unhashable annotation
        return TypeAdapter(type_)


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
    ttl: int | timedelta | None,
    lock_ttl: int | timedelta | None,
    wait_timeout: float | None,
) -> tuple[int | None, int | None, float | None]:
    """Check ``get_or_set``'s arguments and return them normalised."""
    _validate_lock(lock, allow_none=True)
    validated_wait_timeout: float | None = None
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
        validated_wait_timeout = float(wait_timeout)
    return validate_ttl(ttl), validate_ttl(lock_ttl), validated_wait_timeout


class CacheManager:
    """Provides convenient get/set/delete access to the configured cache backend.

    Unlike the ``@cache`` decorator (which caches HTTP response bodies),
    ``CacheManager`` is a thin, JSON-serializing wrapper for caching arbitrary
    application values under a dedicated key namespace.
    """

    def __init__(
        self,
        backend: BaseCacheBackend | None = None,
        key_prefix: str = "cache:",
        default_ttl: int | timedelta | None = None,
        *,
        lock: bool = True,
        lock_ttl: int | timedelta = 60,
    ) -> None:
        r"""Initialize CacheManager.

        Args:
            backend: Cache backend instance. If None, uses BackendProxy.get().
            key_prefix: Prefix prepended to all logical keys in the cache backend.
            default_ttl: Default TTL applied when set() is called without an
                explicit ttl, in seconds or as a ``timedelta``. None means no
                expiry by default.
            lock: Whether get_or_set() uses distributed locking by default to
                prevent cache stampedes (default: True). Pass ``False`` to
                compute on every concurrent miss without the extra backend
                round trips.
            lock_ttl: Default TTL for stampede protection locks, in seconds
                or as a ``timedelta`` (default: 60).

        Raises:
            BackendNotFoundError: If ``backend`` is None and no backend has
                been set with ``BackendProxy.set()``.
            TypeError: If ``lock`` is not a bool, or ``default_ttl``
                or ``lock_ttl`` is not an int or a ``timedelta``.
            ValueError: If ``default_ttl`` or ``lock_ttl`` is zero, negative,
                larger than ``MAX_TTL`` or not a whole number of seconds, or
                ``lock_ttl`` is None.

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

        if lock_ttl is None:
            # Unreachable for type checkers; guards untyped callers.
            msg = "lock_ttl must be a positive int, got None"  # type: ignore[unreachable]
            raise ValueError(msg)
        self.lock: bool = lock
        self.lock_ttl: int = validate_ttl(lock_ttl)

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
        return not GLOB_SPECIAL.isdisjoint(self.key_prefix)

    def _cache_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"

    @staticmethod
    def _encode(value: Any) -> CacheEntry:
        content = json.dumps(value, default=_jsonable).encode("utf-8")
        fingerprint = hashlib.sha256(content).hexdigest()
        return CacheEntry(fingerprint=fingerprint, content=content)

    @overload
    async def get(self, key: str, default: Any = None) -> Any: ...

    @overload
    async def get(
        self, key: str, default: None = None, *, type_: type[T]
    ) -> T | None: ...

    @overload
    async def get(self, key: str, default: D, *, type_: type[T]) -> T | D: ...

    @overload
    async def get(self, key: str, default: Any = None, *, type_: Any) -> Any: ...

    async def get(self, key: str, default: Any = None, *, type_: Any = None) -> Any:
        """Retrieve and JSON-decode a cached value.

        Args:
            key: Logical cache key (without the manager's prefix).
            default: Value returned when the key is missing, expired, or the
                stored content cannot be decoded.
            type_: A type to validate the decoded value into, with pydantic:
                a model, a dataclass, ``datetime``, ``list[Model]``, anything
                ``pydantic.TypeAdapter`` accepts. ``await cache.get("user:1",
                type_=User)`` returns a ``User`` (or ``default``). A stored
                value that does not fit the type, say one written before a
                field was added, counts as a decode failure; so does a stored
                ``None``, unless the type allows it (``User | None``).

        Returns:
            The cached value, or ``default`` on a miss or decode failure.
        """
        cached = await self.backend.get(self._cache_key(key))
        if cached is None:
            return default

        try:
            value = json.loads(cached.content)
            return value if type_ is None else _adapter(type_).validate_python(value)
        except (*_DECODE_ERRORS, ValidationError):
            # Keys often embed user IDs or e-mails: only a digest at WARNING.
            key_ref = log_ref(key)
            logger.warning("Failed to decode cached value; key_ref=%s", key_ref)
            logger.debug(
                "Failed to decode cached value; key_ref=%s key=%s", key_ref, key
            )
            return default

    async def set(
        self, key: str, value: Any, ttl: int | timedelta | None = None
    ) -> None:
        """JSON-encode and store a value in the cache.

        Args:
            key: Logical cache key (without the manager's prefix).
            value: A JSON-serializable Python value, or one pydantic can
                turn into JSON (a model, a ``datetime``, a ``UUID``, a set).
            ttl: Time-to-live in seconds or as a ``timedelta``. If None, uses
                ``self.default_ttl`` (which itself defaults to no expiry).

        Raises:
            TypeError: If ``value`` cannot be encoded as JSON, or ``ttl`` is
                not an int or a ``timedelta``.
            ValueError: If ``ttl`` is zero, negative, larger than ``MAX_TTL``
                or not a whole number of seconds.
        """
        effective_ttl = validate_ttl(ttl if ttl is not None else self.default_ttl)
        entry = self._encode(value)

        await self.backend.set(self._cache_key(key), entry, ttl=effective_ttl)
        logger.debug("Cache SET; key=%s ttl=%s", key, effective_ttl)

    async def add(
        self, key: str, value: Any, ttl: int | timedelta | None = None
    ) -> bool:
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
            value: A JSON-serializable Python value, or one pydantic can
                turn into JSON (a model, a ``datetime``, a ``UUID``, a set).
            ttl: Time-to-live in seconds or as a ``timedelta``. If None, uses
                ``self.default_ttl`` (which itself defaults to no expiry).

        Returns:
            True if the value was stored, False if the key already existed.

        Raises:
            TypeError: If ``value`` cannot be encoded as JSON, or ``ttl`` is
                not an int or a ``timedelta``.
            ValueError: If ``ttl`` is zero, negative, larger than ``MAX_TTL``
                or not a whole number of seconds.
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

    async def increment(
        self, key: str, delta: int = 1, ttl: int | timedelta | None = None
    ) -> int:
        """Atomically add ``delta`` to the counter at ``key`` and return it.

        The backend's atomic ``increment()`` under this manager's prefix, for
        quotas and rate limits. A missing key counts as 0. ``ttl`` applies
        when the counter is created and later calls keep that expiry, so the
        counter lives in a fixed window. ``get()`` reads the counter as an
        ``int``, and ``delete()`` and the ``clear*`` methods remove it.

        Args:
            key: Logical cache key (without the manager's prefix).
            delta: Amount to add; may be negative or zero (zero reads the
                counter, creating it at 0).
            ttl: Time-to-live of a newly created counter, in seconds or as a
                ``timedelta``. If None, uses ``self.default_ttl``.

        Returns:
            The counter's value after the change.

        Raises:
            CacheXError: If ``key`` holds a value that is not a counter, or
                the result would leave the counter's range (see
                ``BaseCacheBackend.increment``).
            TypeError: If ``delta`` is not an int, or ``ttl`` is not an int
                or a ``timedelta``.
            ValueError: If ``ttl`` is zero, negative, larger than ``MAX_TTL``
                or not a whole number of seconds.
        """
        effective_ttl = validate_ttl(ttl if ttl is not None else self.default_ttl)
        return await self.backend.increment(
            self._cache_key(key), delta, ttl=effective_ttl
        )

    async def has(self, key: str) -> bool:
        """Check whether a key exists in the cache without decoding its value.

        Args:
            key: Logical cache key (without the manager's prefix).

        Returns:
            True if the key exists and has not expired.
        """
        return await self.backend.get(self._cache_key(key)) is not None

    async def _lookup(self, key: str, type_: Any) -> Any:
        """``get()`` for ``get_or_set``: the value, or ``_SENTINEL`` on a miss.

        ``type_`` is passed only when given, so a subclass that overrides
        ``get()`` with the signature it had before 0.5.5 keeps working.
        """
        if type_ is None:
            return await self.get(key, default=_SENTINEL)
        return await self.get(key, default=_SENTINEL, type_=type_)

    async def _compute_and_store(
        self,
        key: str,
        factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl: int | None,
        type_: Any = None,
    ) -> Any:
        value = factory()
        if inspect.isawaitable(value):
            value = await value

        effective_ttl = ttl if ttl is not None else self.default_ttl
        entry = self._encode(value)
        await self.backend.set(self._cache_key(key), entry, ttl=effective_ttl)
        logger.debug("Cache SET; key=%s ttl=%s", key, effective_ttl)
        stored = json.loads(entry.content)
        # What a later hit returns. A factory whose value does not fit type_
        # is a bug in the caller, so this ValidationError is not caught.
        return stored if type_ is None else _adapter(type_).validate_python(stored)

    async def _execute_as_winner(
        self,
        key: str,
        factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl: int | None,
        lock_instance: CacheLock,
        type_: Any = None,
    ) -> Any:
        lock_id = f"{id(self.backend)}:{self._cache_key(key)}"
        token = _HELD_LOCKS.set(_HELD_LOCKS.get() | {lock_id})
        try:
            cached = await self._lookup(key, type_)
            if cached is not _SENTINEL:
                return cached
            return await self._compute_and_store(key, factory, ttl, type_)
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
        type_: Any = None,
    ) -> Any:
        start = _monotonic()
        interval = _INITIAL_POLL_INTERVAL
        while True:
            if wait_timeout is not None:
                elapsed = _monotonic() - start
                if elapsed >= wait_timeout:
                    cached = await self._lookup(key, type_)
                    if cached is not _SENTINEL:
                        return cached
                    if raise_on_timeout:
                        key_ref = log_ref(key)
                        logger.debug(
                            "Cache stampede wait timed out; key_ref=%s key=%s",
                            key_ref,
                            key,
                        )
                        msg = (
                            f"Waiting for a cache key timed out after "
                            f"{wait_timeout}s (key_ref={key_ref})"
                        )
                        raise LockTimeoutError(msg)
                    logger.warning(
                        "Cache stampede wait timeout exceeded for key_ref=%s; computing directly",
                        log_ref(key),
                    )
                    return await self._compute_and_store(key, factory, ttl, type_)

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
            cached = await self._lookup(key, type_)
            if cached is not _SENTINEL:
                return cached

            # 2. Otherwise try to take lock
            lock_instance = CacheLock(
                name=self._cache_key(key),
                ttl=lock_ttl,
                backend=self.backend,
            )
            if await lock_instance.acquire(blocking=False):
                return await self._execute_as_winner(
                    key, factory, ttl, lock_instance, type_
                )

    async def get_or_set(  # noqa: PLR0913
        self,
        key: str,
        factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
        ttl: int | timedelta | None = None,
        *,
        lock: bool | None = None,
        lock_ttl: int | timedelta | None = None,
        wait_timeout: float | None = None,
        raise_on_timeout: bool = False,
        type_: Any = None,
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
        dict keys as strings, and a pydantic model, a ``datetime`` or a
        ``UUID`` as the plain JSON value pydantic writes for it. Pass
        ``type_`` to get the typed value back on a miss and on a hit alike.
        A value neither JSON nor pydantic can encode raises ``TypeError``
        only after ``factory`` has run; nothing is stored.

        Args:
            key: Logical cache key (without the manager's prefix).
            factory: Zero-argument callable that produces the JSON-serializable
                value to cache on a miss. If it returns an awaitable (an async
                function, or a lambda or ``functools.partial`` wrapping one),
                the result is awaited.
            ttl: Time-to-live for a newly created value, in seconds or as a
                ``timedelta``. If None, uses ``self.default_ttl``.
            lock: Whether to use distributed locking for stampede protection.
                If None, inherits the manager's ``lock`` setting.
            lock_ttl: Upper bound for the lock lease, in seconds or as a
                ``timedelta``. If None, inherits the manager's ``lock_ttl``.
            wait_timeout: Maximum seconds waiting callers poll the cache before
                timing out. If None, callers wait without a fixed deadline,
                bounded by the holder's lock lease, and attempt to take over the
                lock if it expires.
            raise_on_timeout: If True, raise :exc:`~fastapi_cachex.exceptions.LockTimeoutError`
                when ``wait_timeout`` elapses. If False (default), log a warning and
                fall back to invoking ``factory`` directly.
            type_: A type to validate the value into, as for ``get()``. A
                cached value that does not fit it counts as a miss and is
                recomputed.

        Returns:
            The cached value (existing or newly created), JSON-decoded in
            both cases.

        Raises:
            TypeError: If the value produced by ``factory`` cannot be encoded as JSON,
                or if ``lock``, ``ttl``, ``lock_ttl``, or ``wait_timeout``
                have invalid types.
            ValueError: If ``ttl``, ``lock_ttl``, or ``wait_timeout`` is zero or
                negative, or ``ttl`` or ``lock_ttl`` is larger than ``MAX_TTL``
                or a ``timedelta`` that is not a whole number of seconds.
            LockTimeoutError: If ``raise_on_timeout=True`` and waiting exceeds ``wait_timeout``.
            pydantic.ValidationError: If the value ``factory`` produced does
                not fit ``type_``. It has been stored by then.
        """
        ttl, lock_ttl, validated_wait_timeout = _validate_get_or_set_args(
            lock, ttl, lock_ttl, wait_timeout
        )
        cached = await self._lookup(key, type_)
        if cached is not _SENTINEL:
            return cached

        use_lock = self.lock if lock is None else lock
        if not use_lock:
            return await self._compute_and_store(key, factory, ttl, type_)

        lock_id = f"{id(self.backend)}:{self._cache_key(key)}"
        if lock_id in _HELD_LOCKS.get():
            logger.debug("Re-entrant get_or_set call for key=%s; skipping lock", key)
            return await self._compute_and_store(key, factory, ttl, type_)

        effective_lock_ttl = self.lock_ttl if lock_ttl is None else lock_ttl
        lock_instance = CacheLock(
            name=self._cache_key(key),
            ttl=effective_lock_ttl,
            backend=self.backend,
        )
        if await lock_instance.acquire(blocking=False):
            return await self._execute_as_winner(
                key, factory, ttl, lock_instance, type_
            )

        return await self._poll_for_value(
            key,
            factory,
            ttl,
            effective_lock_ttl,
            validated_wait_timeout,
            raise_on_timeout=raise_on_timeout,
            type_=type_,
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
