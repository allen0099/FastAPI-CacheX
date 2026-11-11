# 分散式鎖 {#distributed-lock}

`CacheLock` 是建立在後端原子操作（`set_if_absent`、`delete_if_equals`、`expire_if_equals`）之上的分散式鎖。只要持有者在鎖的 `ttl` 內保有它，共用同一個快取後端的其他行程或容器就無法取得這把鎖。這把鎖是一份租約，而不是 fencing lock：持有者若超過 `ttl` 仍未續約，就會失去它，其他呼叫者可能在原持有者仍在工作時取得這把鎖（見下方的 **TTL 過期**）。

```python
from fastapi import HTTPException
from fastapi_cachex import CacheLock, LockTimeoutError

# 當作非同步 context manager 使用：
async with CacheLock(f"report:{report_id}", ttl=30):
    ...  # 同一時間只有一個持有者，前提是工作在 ttl 內完成

# 明確呼叫 acquire 與 release。blocking、timeout 與 poll_interval 是建構子的
# 參數；acquire() 可以在每次呼叫時個別覆寫。ttl 也接受 timedelta。
lock = CacheLock(f"stream:{user_id}", ttl=60, blocking=False)
if not await lock.acquire():
    raise HTTPException(409, detail="Lock already held")
try:
    ...
    await lock.extend(60)  # 長時間執行的工作在過期前續約
finally:
    await lock.release()

# 最多等 5 秒，每 100 毫秒檢查一次，之後放棄：
lock = CacheLock(f"report:{report_id}", ttl=30, timeout=5, poll_interval=0.1)
if not await lock.acquire():
    raise HTTPException(503, detail="Try again later")

# 長度未知的工作：在背景續約，直到區塊結束。
async with CacheLock(f"export:{user_id}", ttl=30, auto_extend=True) as lock:
    await build_export()
if lock.lost:
    ...  # 互斥曾經被打破：請檢查或重做結果
```

完整可執行範例（英文）：[`examples/cache_lock.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/cache_lock.py)。

## 行為 {#behavior}

- **安全性與權杖所有權**：每個 `CacheLock` 實例都會產生一個唯一的權杖（`secrets.token_hex(16)`），存放在 `CacheEntry` 中。釋放（`release()`）與續約（`extend()`）都使用會檢查持有者的後端原子操作（`delete_if_equals` 與 `expire_if_equals`），因此鎖已過期的持有者無法釋放或續約已被他人取得的鎖。
- **阻塞與非阻塞模式**：
    - 非阻塞（`acquire(blocking=False)`）：只執行一次原子性的 `set_if_absent`，取得時立即回傳 `True`，已被占用時回傳 `False`。
    - 阻塞（預設；`CacheLock(..., blocking=True, timeout=None, poll_interval=0.1)`，每個值都可以在單次 `acquire()` 呼叫中覆寫，傳入 `None` 表示「沿用實例的值」）：每隔 `poll_interval` 秒重試一次，直到取得鎖或經過 `timeout` 秒為止。預設的 `timeout=None` 會讓阻塞的 `acquire()`（以及 `async with CacheLock(...)`）一直等到鎖被釋放。若到達有限的 `timeout`，`acquire()` 會回傳 `False`。
- **中斷的取得**：若 `acquire()` 在 `set_if_absent` 尚未回應時被取消（請求逾時、用戶端中斷連線）或失敗，後端可能已經存下這次的佔用。此時 `acquire()` 會先以 `delete_if_equals` 刪除它再重新拋出例外，讓鎖不會在 `ttl` 到期前一直被無人持有（0.4.1 之前會）。這個刪除不受第二次取消影響，而且只是盡力而為：若刪除失敗，會在 `fastapi_cachex.lock` logger 記錄一筆 `WARNING`，佔用則隨 `ttl` 過期。
- **Context manager 逾時**：進入 context manager（`async with CacheLock(...)`）時會呼叫 `acquire()`。若取得失敗或逾時，會拋出 `LockTimeoutError`。
- **TTL 過期**：若工作花費的時間超過 `ttl` 且沒有續約，鎖的項目會在後端過期並被釋出。此時其他行程或容器就能在原本的程式碼仍在執行時取得這把鎖。原持有者之後呼叫 `extend()` 或 `release()` 會安全地回傳 `False`，而不會拋出錯誤。請選擇比預期工作時間更長的 `ttl`、在長時間執行的操作中定期呼叫 `extend()`，或傳入 `auto_extend=True`。
- **TTL 續約（`extend`）**：`extend(ttl)` 只在鎖仍由這個持有者實例擁有時才更新鍵的 TTL，避免在已過期的鎖上發生競爭條件。
- **自動續約（`auto_extend=True`）**：`acquire()` 成功後，背景任務會每隔取得鎖時所用 `ttl` 的三分之一呼叫一次 `extend()`，`release()` 會停止它。工作時間沒有已知上限時適合使用；此時 `ttl` 只決定持有者的行程死掉之後，鎖多久會自行釋出。有三個限制：
    - 這個任務在事件迴圈上執行。若阻塞事件迴圈（同步 I/O、長時間的 CPU 運算）超過 `ttl`，鎖一樣會過期。
    - 它會一直續約到 `release()` 為止。持有者若從不釋放，鎖就會在行程存活期間一直被占用，因此請使用 `async with`。
    - 因為連不上後端而失敗的續約，會在 `fastapi_cachex.lock` logger 記錄一筆 `WARNING`，並在下一個間隔重試。一旦整整一個 `ttl` 都沒有續約成功，鎖就已經過期；任務會將它標記為已遺失並停止。
    - Memcached 以整秒計算過期時間，所以那裡的項目實際存活時間最多會比 `ttl` 少一秒，`ttl=1` 可能在第一次續約前就過期。在 Memcached 上請使用至少 2 秒的 `ttl`（這個誤差適用於那裡的每一把鎖，不論是否續約）。
- **偵測鎖已遺失（`lost`）**：當 `extend()`（你自己呼叫的或自動的）或 `release()` 發現鎖已過期或被他人持有時，`lock.lost` 會變成 `True`，並維持到下一次 `acquire()`。在長時間工作途中，或在 `async with` 區塊結束後（它會忽略 `release()` 的結果）讀取它，就能知道是否可能有其他持有者同時執行過。`False` 只代表尚未察覺任何異狀：沒有 `auto_extend` 也沒有自行呼叫 `extend()` 時，過期要到 `release()` 才會被發現。
- **沒有 fencing token**：這把鎖不會發出可讓受保護資源用來拒絕過期持有者寫入的編號。持有者若暫停超過 `ttl`（長時間的 GC 暫停、被暫停的容器）之後才寫入，這把鎖擋不住，有沒有 `auto_extend` 都一樣。如果這樣的寫入會破壞資料，請讓寫入本身帶有條件（版本欄位、compare-and-set）。
- **每次取得使用一個實例**：單一 `CacheLock` 實例會追蹤自己目前的持有狀態。重複進入同一個 `CacheLock` 實例，或在並行的 task 之間共用它，都會拋出 `RuntimeError`。每次取得鎖時請建立新的 `CacheLock` 實例。
- **命名空間**：鎖的鍵預設位於獨立的 `lock:` 前綴下（例如 `lock:report:123`），與 `CacheManager` 的 `cache:` 分開。
- **後端**：未傳入 `backend=` 時，鎖會使用以 `BackendProxy.set()` 註冊的後端。與 `@cache` 不同，它不會改用 `MemoryBackend`：尚未註冊任何後端時，`acquire()` 會拋出 `BackendNotFoundError`。鎖只能排除共用同一個後端的行程，因此每個行程各自一份的 `MemoryBackend` 只能協調同一個行程內的 task。

> [!NOTE]
> `CacheLock` 可用於所有內建後端（`MemoryBackend`、`AsyncRedisCacheBackend`、`MemcachedBackend`）。Redis 使用 `SET NX EX` 與 Lua 腳本，Memcached 使用 `ADD` 與 `CAS`，記憶體後端則在其內部鎖之內操作。

## 跨 worker 每個週期只執行一次 {#once-per-period-across-workers}

「這個工作每小時只跑一次，哪個 worker 先到就由它跑」不適合用鎖：工作結束時鎖就會釋放，下一個走到同一處的 worker 會取得鎖並再跑一次。請改用 [`CacheManager.add()`](APP_CACHE.md) 來認領這個週期，它只在鍵不存在時才以原子操作寫入，再讓鍵隨週期過期：

```python
from datetime import datetime, timezone

from fastapi_cachex import AppCache


async def send_digest_once_per_hour(cache: AppCache) -> None:
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    if await cache.add(f"digest:{hour}", True, ttl=3600):
        # 不論有多少 worker 呼叫，這一小時只有一個會得到 True。
        await send_digest()
```

鍵的名稱就是週期，因此在該小時較晚才啟動的 worker 會發現它已被認領，而 TTL 只要比週期長即可。若工作可能失敗，請在失敗時刪除這個鍵（`await cache.delete(f"digest:{hour}")`），讓其他 worker 可以重試；否則這個週期就會直接略過。只有在工作不能同時執行、但前一次結束後應該立刻可以再跑時，才使用 `CacheLock`。

完整的類別簽章與選項請見 [API 參考](https://fastapi-cachex.readthedocs.io/en/stable/api/lock/)（英文）。
