"""Distributed lock helper for FastAPI-CacheX."""

import asyncio
import contextlib
import logging
import secrets
import time
from types import TracebackType

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.exceptions import LockTimeoutError
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry

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
        ttl: Time to live in seconds (default: 60)
        blocking: Whether acquire() waits for the lock if unavailable (default: True)
        timeout: Maximum seconds to wait in blocking mode (None = wait indefinitely)
        poll_interval: Seconds between retry attempts in blocking mode (default: 0.1)
        backend: Explicit backend instance to use (default: BackendProxy.get())
        key_prefix: Prefix for lock keys (default: "lock:")
    """

    def __init__(
        self,
        name: str,
        ttl: int = 60,
        blocking: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.1,
        backend: BaseCacheBackend | None = None,
        key_prefix: str = "lock:",
    ) -> None:
        """Initialize a CacheLock instance."""
        self.name = name
        self.ttl = ttl
        self.blocking = blocking
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._backend = backend
        self.key_prefix = key_prefix
        self._token = secrets.token_hex(16)
        self._entry: CacheEntry = _lock_entry(self._token)
        self._is_held = False

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
        ttl: int | None = None,
    ) -> bool:
        """Attempt to acquire the lock.

        Args:
            blocking: Override default blocking mode
            timeout: Override default timeout (seconds). If timeout=None, the lock will
                use the instance's default timeout.
            poll_interval: Override default poll interval (seconds)
            ttl: Override default TTL (seconds)

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
        # True while a claim is on its way to the backend: if acquire() is
        # interrupted then, the claim may be stored with nobody to release it.
        claim_in_flight = False
        backend: BaseCacheBackend | None = None
        try:
            is_blocking = self.blocking if blocking is None else blocking
            effective_timeout = self.timeout if timeout is None else timeout
            interval = self.poll_interval if poll_interval is None else poll_interval
            effective_ttl = self.ttl if ttl is None else ttl

            backend = self._get_backend()

            if not is_blocking:
                claim_in_flight = True
                acquired = await backend.set_if_absent(
                    self.key, self._entry, ttl=effective_ttl
                )
                claim_in_flight = False
                if not acquired:
                    self._is_held = False
                return acquired

            start = time.monotonic()
            while True:
                claim_in_flight = True
                if await backend.set_if_absent(
                    self.key, self._entry, ttl=effective_ttl
                ):
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
        logger.warning(
            "CacheLock could not withdraw an interrupted claim; key=%s",
            self.key,
            exc_info=withdrawal.exception(),
        )

    async def release(self) -> bool:
        """Release the lock if still held by this instance.

        Returns:
            True if the lock was released, False if expired or owned by another caller
        """
        self._is_held = False
        backend = self._get_backend()
        released = await backend.delete_if_equals(self.key, self._entry)
        if not released:
            logger.debug(
                "CacheLock release failed for key=%s (lock lost or expired)",
                self.key,
            )
        return released

    async def extend(self, ttl: int | None = None) -> bool:
        """Renew the TTL on the lock if still held by this instance.

        Args:
            ttl: New TTL in seconds (None = use default instance TTL)

        Returns:
            True if the TTL was updated, False if expired or owned by another caller
        """
        effective_ttl = self.ttl if ttl is None else ttl
        backend = self._get_backend()
        return await backend.expire_if_equals(self.key, self._entry, ttl=effective_ttl)

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
            msg = f"Failed to acquire lock '{self.name}' within timeout"
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
