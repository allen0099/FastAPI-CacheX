# Distributed Lock

`CacheLock` provides a distributed lock helper built on backend atomic primitives (`set_if_absent`, `delete_if_equals`, `expire_if_equals`). It guarantees mutual exclusion across multiple processes or containers sharing the same cache backend.

```python
from fastapi import HTTPException
from fastapi_cachex import CacheLock, LockTimeoutError

# Usage as an async context manager:
async with CacheLock(f"report:{report_id}", ttl=30):
    ...  # only one process holds the lock at any time

# Explicit acquire and release calls:
lock = CacheLock(f"stream:{user_id}", ttl=60)
if not await lock.acquire(blocking=False):
    raise HTTPException(409, detail="Lock already held")
try:
    ...
    await lock.extend(60)  # long-running tasks renew before expiry
finally:
    await lock.release()
```

## Behavior

- **Safety & Token Ownership**: Each `CacheLock` instance generates a unique token (`secrets.token_hex(16)`) stored inside a `CacheEntry`. Releases (`release()`) and extensions (`extend()`) use owner-checked backend primitives (`delete_if_equals` and `expire_if_equals`), so a holder whose lock expired cannot release or renew a lock claimed by someone else.
- **Blocking & Non-blocking Modes**:
  - Non-blocking (`acquire(blocking=False)`): Performs a single atomic `set_if_absent` and immediately returns `True` if acquired or `False` if held.
  - Blocking (`acquire(blocking=True, timeout=None, poll_interval=0.1)`): Retries at `poll_interval` seconds until acquired or until `timeout` seconds elapse. If `timeout` is reached, `acquire()` returns `False`.
- **Context Manager Timeouts**: Entering a context manager (`async with CacheLock(...)`) invokes `acquire()`. If acquisition fails or times out, it raises `LockTimeoutError`.
- **TTL Renewal (`extend`)**: `extend(ttl)` updates the key's TTL only while the lock is still owned by this holder instance, preventing race conditions on expired locks.
- **One Instance per Acquisition Rule**: A single `CacheLock` instance tracks its active ownership state. Re-entering or sharing a single `CacheLock` instance across concurrent tasks raises a `RuntimeError`. Instantiate a new `CacheLock` instance for each acquisition.
- **Namespace**: Lock keys live under their own `lock:` prefix by default (e.g. `lock:report:123`), separate from `cache:` and `oauth_state:`.

> [!NOTE]
> `CacheLock` works on all built-in backends (`MemoryBackend`, `AsyncRedisCacheBackend`, `MemcachedBackend`).
> Redis executes Lua scripts for atomic operations, Memcached uses `ADD` and `CAS`, and the memory backend operates under its internal lock.

The full class signature and options are documented in the [API reference](api/lock.md).
