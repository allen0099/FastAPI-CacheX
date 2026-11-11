"""Distributed lock helper for FastAPI-CacheX."""

import asyncio
import contextlib
import logging
import secrets
import time
from datetime import timedelta
from types import TracebackType

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.base import validate_ttl
from fastapi_cachex.exceptions import LockTimeoutError
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry
from fastapi_cachex.types import log_ref

logger = logging.getLogger(__name__)

# Fingerprint every backend reports for a key that holds a lock entry
_LOCK_FINGERPRINT = "lock"

# Withdrawals still running after a second cancellation; the event loop keeps
# only a weak reference to a task.
_pending_withdrawals: set["asyncio.Task[bool]"] = set()


def _lock_entry(token: str) -> CacheEntry:
    """Wrap a lock token string in the entry model shared by every backend."""
    return CacheEntry(fingerprint=_LOCK_FINGERPRINT, content=token.encode("utf-8"))


class CacheLock:
    """Distributed lock helper built on backend primitives.

    Can be used as an async context manager or with direct acquire/release calls.

    Note:
        A single CacheLock instance cannot be shared across concurrent tasks or
        re-entered. Instantiate a new CacheLock for each acquisition.

    Args:
        name: Unique lock identifier
        ttl: Time to live in seconds or as a ``timedelta`` (default: 60)
        blocking: Whether acquire() waits for the lock if unavailable (default: True)
        timeout: Maximum seconds to wait in blocking mode (None = wait indefinitely)
        poll_interval: Seconds between retry attempts in blocking mode (default: 0.1)
        backend: Explicit backend instance to use (default: BackendProxy.get())
        key_prefix: Prefix for lock keys (default: "lock:")
        auto_extend: Renew the lock in the background while it is held, every
            third of its ``ttl``, until ``release()`` (default: False). For
            work whose duration is not known in advance. The renewal task
            runs on the event loop, so it cannot run while the holder blocks
            that loop; and it runs until ``release()``, so a holder that
            never releases keeps the lock for as long as its process lives.
            Use ``async with``, and read ``lost`` to learn that a renewal
            failed.

    Raises:
        TTLTypeError: If ``ttl`` is not an ``int`` or a ``timedelta`` (also
            a ``TypeError``)
        TTLValueError: If ``ttl`` is zero, negative, larger than ``MAX_TTL``
            or not a whole number of seconds (also a ``ValueError``)
    """

    def __init__(
        self,
        name: str,
        ttl: int | timedelta = 60,
        blocking: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.1,
        backend: BaseCacheBackend | None = None,
        key_prefix: str = "lock:",
        *,
        auto_extend: bool = False,
    ) -> None:
        """Initialize a CacheLock instance."""
        self.name = name
        self.ttl: int = validate_ttl(ttl)
        self.blocking = blocking
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._backend = backend
        self.key_prefix = key_prefix
        self._token = secrets.token_hex(16)
        self._entry: CacheEntry = _lock_entry(self._token)
        self._is_held = False
        self.auto_extend = auto_extend
        self._lost = False
        self._renewal: asyncio.Task[None] | None = None

    @property
    def lost(self) -> bool:
        """Whether this holder learned that its lock was no longer its own.

        True once ``extend()`` (called by you or by ``auto_extend``) or
        ``release()`` found the lock expired or taken by someone else, or
        once ``auto_extend`` could not reach the backend for a whole ``ttl``.
        The work done since the last successful renewal may have overlapped
        another holder's. False means only that nothing has been noticed:
        without a renewal in between, an expiry shows up at ``release()``.
        Reset by the next ``acquire()``.
        """
        return self._lost

    @property
    def key(self) -> str:
        """The fully-prefixed backend key for this lock."""
        return f"{self.key_prefix}{self.name}"

    def _get_backend(self) -> BaseCacheBackend:
        """Return configured backend or resolve via BackendProxy.get()."""
        if self._backend is not None:
            return self._backend
        return BackendProxy.get()

    async def acquire(
        self,
        blocking: bool | None = None,
        timeout: float | None = None,
        poll_interval: float | None = None,
        ttl: int | timedelta | None = None,
    ) -> bool:
        """Attempt to acquire the lock.

        Args:
            blocking: Override default blocking mode
            timeout: Override default timeout (seconds). If timeout=None, the lock will
                use the instance's default timeout.
            poll_interval: Override default poll interval (seconds)
            ttl: Override default TTL (seconds or a ``timedelta``)

        Returns:
            True if the lock was acquired; False if it is held elsewhere
            (non-blocking) or the timeout elapsed first (blocking)

        Raises:
            RuntimeError: If this CacheLock instance is already held.
            BackendNotFoundError: If no ``backend`` was passed and none is
                registered with ``BackendProxy.set()``.
        """
        if self._is_held:
            msg = (
                "This CacheLock instance is already held. Use a new CacheLock instance "
                "for each acquisition."
            )
            raise RuntimeError(msg)

        self._is_held = True
        self._lost = False
        # True while a claim is on its way to the backend: if acquire() is
        # interrupted then, the claim may be stored with nobody to release it.
        claim_in_flight = False
        backend: BaseCacheBackend | None = None
        try:
            is_blocking = self.blocking if blocking is None else blocking
            effective_timeout = self.timeout if timeout is None else timeout
            interval = self.poll_interval if poll_interval is None else poll_interval
            effective_ttl = self.ttl if ttl is None else validate_ttl(ttl)

            backend = self._get_backend()

            if not is_blocking:
                claim_in_flight = True
                acquired = await backend.set_if_absent(
                    self.key, self._entry, ttl=effective_ttl
                )
                claim_in_flight = False
                if acquired:
                    self._start_renewal(effective_ttl)
                else:
                    self._is_held = False
                return acquired

            start = time.monotonic()
            while True:
                claim_in_flight = True
                if await backend.set_if_absent(
                    self.key, self._entry, ttl=effective_ttl
                ):
                    self._start_renewal(effective_ttl)
                    return True
                claim_in_flight = False
                if effective_timeout is not None:
                    elapsed = time.monotonic() - start
                    if elapsed >= effective_timeout:
                        self._is_held = False
                        return False
                    remaining = effective_timeout - elapsed
                    sleep_time = min(interval, remaining)
                else:
                    sleep_time = interval
                await asyncio.sleep(sleep_time)
        except BaseException:
            self._is_held = False
            if claim_in_flight and backend is not None:
                await self._withdraw_claim(backend)
            raise

    async def _withdraw_claim(self, backend: BaseCacheBackend) -> None:
        """Remove a claim that may have reached the backend before an error (#234).

        A cancellation (a request timeout, a client disconnect) or a transport
        error can interrupt ``set_if_absent`` after the backend stored the
        claim, which would leave the key held by nobody until its TTL ran
        out. The token is unique to this instance, so deleting it only if it
        is still there cannot release anyone else's lock. Shielded, so a
        second cancellation does not stop the delete; best effort, so a
        failure is logged and the original exception still propagates. The
        failure is logged from the task itself, so it is logged even when a
        second cancellation stopped this method from waiting for the delete.
        """
        withdrawal = asyncio.ensure_future(
            backend.delete_if_equals(self.key, self._entry)
        )
        _pending_withdrawals.add(withdrawal)
        withdrawal.add_done_callback(self._log_failed_withdrawal)
        # A failure is logged by _log_failed_withdrawal.
        with contextlib.suppress(Exception):
            await asyncio.shield(withdrawal)

    def _log_failed_withdrawal(self, withdrawal: "asyncio.Task[bool]") -> None:
        """Log a withdrawal that raised; reading the exception also retrieves it."""
        _pending_withdrawals.discard(withdrawal)
        if withdrawal.cancelled() or withdrawal.exception() is None:
            return
        # Lock names often embed user IDs or e-mails: only a digest at WARNING.
        key_ref = log_ref(self.key)
        logger.warning(
            "CacheLock could not withdraw an interrupted claim; key_ref=%s",
            key_ref,
            exc_info=withdrawal.exception(),
        )
        logger.debug(
            "CacheLock could not withdraw an interrupted claim; key_ref=%s key=%s",
            key_ref,
            self.key,
        )

    def _start_renewal(self, ttl: int) -> None:
        """Start the ``auto_extend`` task for a lock just acquired with ``ttl``."""
        if self.auto_extend:
            self._renewal = asyncio.create_task(self._renew(ttl))

    async def _renew(self, ttl: int) -> None:
        """Extend the lock every ``ttl / 3`` until it is lost or released.

        A third leaves two more attempts before the lock expires when one
        fails. A backend error is logged and retried; once a whole ``ttl``
        has passed without a renewal the lock has expired, whatever the
        backend would say, and the task marks it lost and stops.
        """
        renewed_at = time.monotonic()
        while True:
            await asyncio.sleep(ttl / 3)
            try:
                renewed = await self.extend(ttl)
            except Exception:
                # Lock names often embed user IDs or e-mails: only a digest.
                logger.warning(
                    "CacheLock could not renew its lock; key_ref=%s",
                    log_ref(self.key),
                    exc_info=True,
                )
                if time.monotonic() - renewed_at < ttl:
                    continue
                renewed = False
            if not renewed:
                self._lost = True
                logger.warning(
                    "CacheLock lost its lock before release; key_ref=%s",
                    log_ref(self.key),
                )
                return
            renewed_at = time.monotonic()

    async def _stop_renewal(self) -> None:
        """Cancel the ``auto_extend`` task and wait until it has stopped."""
        renewal, self._renewal = self._renewal, None
        if renewal is None or renewal.done():
            return
        renewal.cancel()
        # wait() does not raise the task's CancelledError, so a cancellation
        # of release() itself still propagates.
        await asyncio.wait([renewal])

    async def release(self) -> bool:
        """Release the lock if still held by this instance.

        Stops ``auto_extend`` first. A False result for a lock this instance
        held also sets ``lost``.

        Returns:
            True if the lock was released, False if expired or owned by another caller
        """
        was_held, self._is_held = self._is_held, False
        await self._stop_renewal()
        backend = self._get_backend()
        released = await backend.delete_if_equals(self.key, self._entry)
        if not released:
            # A second release(), or one without acquire(), lost nothing.
            self._lost = self._lost or was_held
            logger.debug(
                "CacheLock release failed for key=%s (lock lost or expired)",
                self.key,
            )
        return released

    async def extend(self, ttl: int | timedelta | None = None) -> bool:
        """Renew the TTL on the lock if still held by this instance.

        Args:
            ttl: New TTL in seconds or as a ``timedelta`` (None = use default
                instance TTL)

        A False result for a lock this instance held also sets ``lost``.

        Returns:
            True if the TTL was updated, False if expired or owned by another caller
        """
        effective_ttl = self.ttl if ttl is None else validate_ttl(ttl)
        backend = self._get_backend()
        extended = await backend.expire_if_equals(
            self.key, self._entry, ttl=effective_ttl
        )
        if not extended:
            self._lost = self._lost or self._is_held
        return extended

    async def locked(self) -> bool:
        """Check whether the lock is currently held by any holder.

        Returns:
            True if the lock key currently exists in the backend, False otherwise
        """
        backend = self._get_backend()
        return (await backend.get(self.key)) is not None

    async def __aenter__(self) -> "CacheLock":  # noqa: PYI034
        """Acquire lock as async context manager.

        Raises:
            LockTimeoutError: If the lock cannot be acquired within the timeout
        """
        acquired = await self.acquire()
        if not acquired:
            # An unhandled timeout reaches the error log: a digest, not the name.
            key_ref = log_ref(self.key)
            logger.debug("CacheLock timed out; key_ref=%s key=%s", key_ref, self.key)
            msg = f"Failed to acquire lock within timeout (key_ref={key_ref})"
            raise LockTimeoutError(msg)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Release lock when exiting async context manager."""
        await self.release()
