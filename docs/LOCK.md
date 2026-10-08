# Distributed Lock

`CacheLock` is a distributed lock built on backend atomic primitives (`set_if_absent`, `delete_if_equals`, `expire_if_equals`). While a holder keeps its lock within the lock's `ttl`, no other process or container sharing the same cache backend can acquire it. The lock is a lease, not a fencing lock: a holder that runs past the `ttl` without renewing loses it, and another caller may acquire it while the first is still working (see **TTL Expiration** below).

```python
from fastapi import HTTPException
from fastapi_cachex import CacheLock, LockTimeoutError

# Usage as an async context manager:
async with CacheLock(f"report:{report_id}", ttl=30):
    ...  # one holder at a time, as long as the work fits in the ttl

# Explicit acquire and release calls. blocking, timeout and poll_interval are
# constructor arguments; acquire() can override each one per call. ttl also
# takes a timedelta.
lock = CacheLock(f"stream:{user_id}", ttl=60, blocking=False)
if not await lock.acquire():
    raise HTTPException(409, detail="Lock already held")
try:
    ...
    await lock.extend(60)  # long-running tasks renew before expiry
finally:
    await lock.release()

# Wait up to 5 seconds, checking every 100 ms, then give up:
lock = CacheLock(f"report:{report_id}", ttl=30, timeout=5, poll_interval=0.1)
if not await lock.acquire():
    raise HTTPException(503, detail="Try again later")
```

Complete runnable example: [`examples/cache_lock.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/cache_lock.py).

## Behavior

- **Safety & Token Ownership**: Each `CacheLock` instance generates a unique token (`secrets.token_hex(16)`) stored inside a `CacheEntry`. Releases (`release()`) and extensions (`extend()`) use owner-checked backend primitives (`delete_if_equals` and `expire_if_equals`), so a holder whose lock expired cannot release or renew a lock claimed by someone else.
- **Blocking & Non-blocking Modes**:
    - Non-blocking (`acquire(blocking=False)`): Performs a single atomic `set_if_absent` and immediately returns `True` if acquired or `False` if held.
    - Blocking (the default; `CacheLock(..., blocking=True, timeout=None, poll_interval=0.1)`, each overridable per `acquire()` call, where `None` means "use the instance's value"): Retries at `poll_interval` seconds until acquired or until `timeout` seconds elapse. The default `timeout=None` makes a blocking `acquire()` (and `async with CacheLock(...)`) wait indefinitely until the lock becomes free. If a finite `timeout` is reached, `acquire()` returns `False`.
- **Interrupted Acquisition**: If `acquire()` is cancelled (a request timeout, a client disconnect) or fails while its `set_if_absent` is in flight, the backend may already have stored the claim. `acquire()` then deletes it with `delete_if_equals` before re-raising, so the lock does not stay held by nobody until its `ttl` runs out (before 0.4.1 it did). The delete is shielded from a second cancellation and is best effort: if it fails, a `WARNING` is logged on the `fastapi_cachex.lock` logger and the claim expires with its `ttl`.
- **Context Manager Timeouts**: Entering a context manager (`async with CacheLock(...)`) invokes `acquire()`. If acquisition fails or times out, it raises `LockTimeoutError`.
- **TTL Expiration**: If a task takes longer than its `ttl` and fails to renew, the lock entry expires in the backend and becomes free. Another process or container can then acquire the lock while the original code is still running. Subsequent calls to `extend()` or `release()` by the original holder will safely return `False` without throwing an error. Always choose a `ttl` longer than the expected work, or call `extend()` periodically during long-running operations.
- **TTL Renewal (`extend`)**: `extend(ttl)` updates the key's TTL only while the lock is still owned by this holder instance, preventing race conditions on expired locks.
- **One Instance per Acquisition Rule**: A single `CacheLock` instance tracks its active ownership state. Re-entering or sharing a single `CacheLock` instance across concurrent tasks raises a `RuntimeError`. Instantiate a new `CacheLock` instance for each acquisition.
- **Namespace**: Lock keys live under their own `lock:` prefix by default (e.g. `lock:report:123`), separate from `CacheManager`'s `cache:`.
- **Backend**: Without `backend=`, a lock uses the backend registered with `BackendProxy.set()`. Unlike `@cache`, it does not fall back to a `MemoryBackend`: with no backend registered, `acquire()` raises `BackendNotFoundError`. A lock only excludes processes that share its backend, so a per-process `MemoryBackend` only coordinates tasks within one process.

> [!NOTE]
> `CacheLock` works on all built-in backends (`MemoryBackend`, `AsyncRedisCacheBackend`, `MemcachedBackend`).
> Redis uses `SET NX EX` and Lua scripts, Memcached uses `ADD` and `CAS`, and the memory backend operates under its internal lock.

## Once per period across workers

A lock is the wrong tool for "run this job once an hour, whichever worker gets
there first": the lock is released when the job ends, and the next worker to
reach the same point takes it and runs the job again. Claim the period instead
with [`CacheManager.add()`](APP_CACHE.md), which stores a key only if it is
absent, atomically, and let the key expire with the period:

```python
from datetime import datetime, timezone

from fastapi_cachex import AppCache


async def send_digest_once_per_hour(cache: AppCache) -> None:
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    if await cache.add(f"digest:{hour}", True, ttl=3600):
        # Exactly one worker gets True for this hour, however many call this.
        await send_digest()
```

The key names the period, so a worker that starts late in the hour finds it
taken, and the TTL only has to outlive the period. If the job can fail, delete
the key in that case (`await cache.delete(f"digest:{hour}")`) so another
worker may retry; otherwise the period is simply skipped. Use a `CacheLock`
when the job must not run concurrently but should run again as soon as the
previous run is over.

The full class signature and options are documented in the [API reference](api/lock.md).
