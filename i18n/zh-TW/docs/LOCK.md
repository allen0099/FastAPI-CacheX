# 分散式鎖 {#distributed-lock}

`CacheLock` 是建立在後端原子操作（`set_if_absent`、`delete_if_equals`、`expire_if_equals`）之上的分散式鎖。只要持有者在鎖的 `ttl` 內保有它，共用同一個快取後端的其他行程或容器就無法取得這把鎖。這把鎖是一份租約，而不是 fencing lock：持有者若超過 `ttl` 仍未續約，就會失去它，其他呼叫者可能在原持有者仍在工作時取得這把鎖（見下方的 **TTL 過期**）。

```python
from fastapi import HTTPException
from fastapi_cachex import CacheLock, LockTimeoutError

# 當作非同步 context manager 使用：
async with CacheLock(f"report:{report_id}", ttl=30):
    ...  # 同一時間只有一個持有者，前提是工作在 ttl 內完成

# 明確呼叫 acquire 與 release：
lock = CacheLock(f"stream:{user_id}", ttl=60)
if not await lock.acquire(blocking=False):
    raise HTTPException(409, detail="Lock already held")
try:
    ...
    await lock.extend(60)  # 長時間執行的工作在過期前續約
finally:
    await lock.release()
```

## 行為 {#behavior}

- **安全性與權杖所有權**：每個 `CacheLock` 實例都會產生一個唯一的權杖（`secrets.token_hex(16)`），存放在 `CacheEntry` 中。釋放（`release()`）與續約（`extend()`）都使用會檢查持有者的後端原子操作（`delete_if_equals` 與 `expire_if_equals`），因此鎖已過期的持有者無法釋放或續約已被他人取得的鎖。
- **阻塞與非阻塞模式**：
    - 非阻塞（`acquire(blocking=False)`）：只執行一次原子性的 `set_if_absent`，取得時立即回傳 `True`，已被占用時回傳 `False`。
    - 阻塞（`acquire(blocking=True, timeout=None, poll_interval=0.1)`）：每隔 `poll_interval` 秒重試一次，直到取得鎖或經過 `timeout` 秒為止。預設的 `timeout=None` 會讓阻塞的 `acquire()`（以及 `async with CacheLock(...)`）一直等到鎖被釋放。若到達有限的 `timeout`，`acquire()` 會回傳 `False`。
- **Context manager 逾時**：進入 context manager（`async with CacheLock(...)`）時會呼叫 `acquire()`。若取得失敗或逾時，會拋出 `LockTimeoutError`。
- **TTL 過期**：若工作花費的時間超過 `ttl` 且沒有續約，鎖的項目會在後端過期並被釋出。此時其他行程或容器就能在原本的程式碼仍在執行時取得這把鎖。原持有者之後呼叫 `extend()` 或 `release()` 會安全地回傳 `False`，而不會拋出錯誤。請選擇比預期工作時間更長的 `ttl`，或在長時間執行的操作中定期呼叫 `extend()`。
- **TTL 續約（`extend`）**：`extend(ttl)` 只在鎖仍由這個持有者實例擁有時才更新鍵的 TTL，避免在已過期的鎖上發生競爭條件。
- **每次取得使用一個實例**：單一 `CacheLock` 實例會追蹤自己目前的持有狀態。重複進入同一個 `CacheLock` 實例，或在並行的 task 之間共用它，都會拋出 `RuntimeError`。每次取得鎖時請建立新的 `CacheLock` 實例。
- **命名空間**：鎖的鍵預設位於獨立的 `lock:` 前綴下（例如 `lock:report:123`），與 `cache:` 及 `oauth_state:` 分開。
- **後端**：未傳入 `backend=` 時，鎖會使用以 `BackendProxy.set()` 註冊的後端。與 `@cache` 不同，它不會改用 `MemoryBackend`：尚未註冊任何後端時，`acquire()` 會拋出 `BackendNotFoundError`。鎖只能排除共用同一個後端的行程，因此每個行程各自一份的 `MemoryBackend` 只能協調同一個行程內的 task。

> [!NOTE]
> `CacheLock` 可用於所有內建後端（`MemoryBackend`、`AsyncRedisCacheBackend`、`MemcachedBackend`）。Redis 使用 `SET NX EX` 與 Lua 腳本，Memcached 使用 `ADD` 與 `CAS`，記憶體後端則在其內部鎖之內操作。

完整的類別簽章與選項請見 [API 參考](https://fastapi-cachex.readthedocs.io/en/latest/api/lock/)（英文）。
