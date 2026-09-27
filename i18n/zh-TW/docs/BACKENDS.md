# 後端 {#backends}

所有快取，包括 HTTP 回應、`CacheManager` 的值、Session 與 OAuth state，都存放在同一個後端，並在啟動時以 `BackendProxy.set()` 註冊一次。

## 選擇後端 {#choosing-a-backend}

| 情境 | 建議後端 | 原因 |
|----------|---------------------|-----|
| 開發與測試 | MemoryBackend | 快速，無外部依賴 |
| 分散式系統 | Redis | 非同步、有效率，支援依模式清除 |
| 簡單快取 | Memcached | 穩定、成熟（但無法列舉鍵，因此不支援依模式／路徑清除，也無法監控） |
| 多行程部署 | Redis | 共用快取、一致性 |

Redis 與 Memcached 後端會以前綴為鍵建立命名空間（預設為 `fastapi_cachex:`，可用 `key_prefix=` 變更），以避免與同一台伺服器上的其他應用程式衝突。`MemoryBackend` 只存在於單一行程內，沒有前綴。

## 記憶體（預設） {#in-memory-default}

若未指定後端，FastAPI-CacheX 預設會使用記憶體快取。這適合開發與測試用途。此後端會自動執行清理工作，每 60 秒移除一次已過期的項目（`MemoryBackend(cleanup_interval=60)`；間隔必須大於 0）。

```python
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex import BackendProxy

backend = MemoryBackend()
BackendProxy.set(backend)
```

> [!NOTE]
> 記憶體快取不適合用於多行程的正式環境。每個行程都各自維護獨立的快取。

清理 task 會在第一次快取呼叫所在的事件迴圈（event loop）上啟動。若之後的呼叫在另一個迴圈上執行（例如第一個迴圈已關閉），task 會在新的迴圈上重新啟動。關閉應用程式時，`await backend.aclose()` 會取消 task 並等待它結束（參見[關閉後端](#closing-a-backend)）；`stop_cleanup()` 只會要求取消。

`clear_pattern()` 在所有平台上都以區分大小寫的方式比對完整的鍵，與 Redis 相同。萬用字元語法採用 Python 的 `fnmatch`，與 Redis 有兩處不同：否定字元類別要寫 `[!...]`（Redis 為 `[^...]`）；跳脫特殊字元要放進中括號，例如 `[*]`（Redis 另外也接受 `\*`）。`*`、`?` 與 `[abc]` 在兩者上的行為相同。

## Redis {#redis}

以 `uv add "fastapi-cachex[redis]"` 安裝此 extra。

```python
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex import BackendProxy

backend = AsyncRedisCacheBackend(host="127.0.0.1", port=6379, db=0)
BackendProxy.set(backend)
```

- 完全非同步的實作
- 支援依模式清除鍵
- 使用 SCAN 而非 KEYS，可安全用於正式環境（不會阻塞）
- 預設以 `fastapi_cachex:` 前綴建立命名空間；多租戶情境可傳入 `key_prefix="myapp:cache:"`
- 只有傳給 `clear_pattern()` 的模式是萬用字元（glob）模式。鍵前綴與傳給 `clear_path()` 的路徑都以字面值比對，因此其中的 `*`、`?`、`[` 或 `]` 不會觸及前綴以外的鍵，也不會漏掉該路徑
- `clear_pattern()` 比對的是邏輯鍵，也就是不含後端前綴的鍵，並一律自行加上前綴。0.3.8 以前，以前綴開頭的模式會先去掉前綴再比對。在只有這種寫法能比對到項目時，它仍可使用，但會發出 `DeprecationWarning`，直到 0.4.0 為止

**從模型設定**：`RedisConfig` 是具有相同設定項與驗證的 pydantic 模型，當設定來自環境變數或設定檔時很方便：

```python
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.backends.config import RedisConfig

config = RedisConfig(
    host="127.0.0.1",
    port=6379,
    password=None,  # SecretStr | None
    db=0,
    encoding="utf-8",  # 保持 UTF-8；見下方說明
    socket_timeout=1.0,  # 秒；適用於讀取／寫入
    socket_connect_timeout=1.0,
    key_prefix="fastapi_cachex:",
    protocol=2,  # RESP 版本，2 或 3
)
backend = AsyncRedisCacheBackend.load_from_config(config)
BackendProxy.set(backend)
```

請保持 `encoding="utf-8"`。項目一律以 UTF-8 JSON 寫入，而用戶端會以 `encoding` 解碼回應，因此任何其他值都會在讀回時破壞非 ASCII 內容（使用 `"latin-1"` 時，儲存的 `b"\xe9"` 會讀回成 `b"\xc3\xa9"`）。編碼不是 UTF-8 時，後端會發出 `RuntimeWarning`，而這個參數將於 0.4.0 移除。

除非你需要 RESP3 的功能，*而且*你的 `hiredis` 建置支援它（RESP3 需要 hiredis >= 3.0），否則請保留 `protocol=2`。Redis 8.0 支援 RESP3，但較舊的 hiredis 會無法協商使用它。

完整可執行範例（英文）：[`examples/redis_backend.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/redis_backend.py)。

## Memcached {#memcached}

以 `uv add "fastapi-cachex[memcached]"` 安裝此 extra。0.3.8 以前這個 extra 名為 `memcache`；舊名稱仍可使用但已棄用，將於 0.4.0 移除。安裝時遇到不存在的 extra 只會顯示警告，因此 0.4.0 之後 `fastapi-cachex[memcache]` 會裝好套件但不含 `pymemcache`。

```python
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex import BackendProxy

backend = MemcachedBackend(servers=["localhost:11211"])
BackendProxy.set(backend)
```

**限制**：

- Memcached 協定不支援依模式清除鍵（`clear_pattern`）
- 無法列舉鍵：`get_all_keys()`／`get_cache_data()` 會回傳空結果（並發出 `RuntimeWarning`），因此監控路由不會顯示任何內容
- `clear_path()` 只會刪除完全相符的那個鍵；`include_params` 沒有作用
- `clear()` 會發出 `flush_all`，清空整台 Memcached 伺服器，而不只是這個命名空間
- Memcached 會拒絕的鍵（超過 250 位元組、含空白字元或非 ASCII 字元）會改以其 SHA-256 摘要儲存
- 過期時間落在 2038-01-19 之後的 `ttl` 會拋出 `ValueError`（見 [TTL 值](#ttl-values)）
- 超過伺服器項目大小上限（預設 1 MB，可用 `memcached -I` 調整）的值會被拒絕並拋出錯誤。`@cache` 會記錄該錯誤，並照常送出不儲存的回應（見[後端發生錯誤時](HTTP_CACHING.md#when-the-backend-fails)）；其他呼叫端則會收到該錯誤
- 若需要依模式清除快取，請考慮使用 Redis 後端

同步的 pymemcache 用戶端在工作執行緒中執行，並使用連線池，因此並行的請求絕不會共用同一個 socket。寫入會等待伺服器確認（`default_noreply=False`），因此只要 `set()` 返回，就能從連線池中的任何連線讀到該值。每次呼叫只切換到工作執行緒一次，多步驟的[原子操作](#atomic-backend-primitives)也是如此。`delete_many()` 在這一次呼叫中逐一對每個鍵送出 `DELETE`，並回傳實際存在的鍵數；0.3.8 之前它每個鍵都要切換一次執行緒，回傳的是傳入的鍵數。

伺服器無法連線時，所有要送往它的呼叫都會拋出錯誤，一秒後會再嘗試連線。0.3.8 以前，失敗後一秒內的呼叫會回傳虛構的結果：`get()` 當成未命中、`set()` 沒有任何反應（寫入遺失）、`increment()` 當成新的計數器並回傳 0。設定多台伺服器時，失敗的那台會立即移出輪替，它的鍵會改由其餘伺服器處理，直到它恢復回應。

## 關閉後端 {#closing-a-backend}

每個後端都有 `aclose()`，用來釋放它持有的連線與背景工作。請在關閉應用程式時，於 FastAPI lifespan 的結尾呼叫它：

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fastapi_cachex import BackendProxy
from fastapi_cachex.backends import AsyncRedisCacheBackend


@asynccontextmanager
async def lifespan(app: FastAPI):
    backend = AsyncRedisCacheBackend(host="127.0.0.1", port=6379)
    BackendProxy.set(backend)
    try:
        yield
    finally:
        BackendProxy.set(None)
        await backend.aclose()


app = FastAPI(lifespan=lifespan)
```

每種後端都適用同一個 lifespan。後端也是非同步 context manager，因此 `async with MemcachedBackend(servers=[...]) as backend:` 會在區塊結束時關閉它，區塊拋出例外時也一樣。

| 後端 | `aclose()` |
|------|------------|
| `MemoryBackend` | 取消清理 task 並等待它結束 |
| `AsyncRedisCacheBackend` | 關閉 redis-py 用戶端，以及它自己建立的連線池 |
| `MemcachedBackend` | 在工作執行緒中關閉連往每台伺服器的所有連線池 socket |

- 多次呼叫 `aclose()` 是安全的。
- 後端擁有它自己建立的用戶端，因此 `aclose()` 也會關閉 `backend.client`；請關閉後端，而不要直接操作用戶端。你自行建立並以 `connection_pool=` 傳入的 Redis 連線池不會被關閉，與 redis-py 的做法相同：誰建立連線池，就由誰關閉。
- 關閉後的後端並不會被鎖住：Redis 與 Memcached 用戶端會在下一次呼叫時重新連線，`MemoryBackend` 也會重新啟動清理 task，因此在 `aclose()` 之後又使用的後端需要再呼叫一次 `aclose()`。
- `BackendProxy.set(None)` 只會取消註冊後端，不會關閉它。
- 自訂後端會從 `BaseCacheBackend` 繼承一個什麼都不做的 `aclose()`；若後端持有連線或背景工作，請覆寫它。
- 若不呼叫 `aclose()`，開啟中的 socket 只會由垃圾回收器關閉，關閉應用程式時可能會產生 `ResourceWarning`。0.3.9 之前只有 `MemoryBackend` 有 `aclose()`，Redis 與 Memcached 後端要透過 `backend.client` 關閉。

## 後端的原子操作 {#atomic-backend-primitives}

每個後端都在 `get`／`set`／`delete` 之上提供原子操作，供會被許多並行請求讀寫的值使用：

```python
import secrets

from fastapi_cachex import BackendProxy
from fastapi_cachex.types import CacheEntry

backend = BackendProxy.get()

# 固定時間窗計數器：首次使用時建立，`ttl` 只在那時套用。
hits = await backend.increment(f"resend:{user_id}", ttl=86400)
if hits > 3:
    raise TooManyRequests()

# 一次性的值：多個並行呼叫者中恰好只有一個會取得該項目。
grant = await backend.get_and_delete(f"grant:{token}")

# 鎖／名額：只在未被占用時取得，且只在仍屬於你時釋放。
owner = CacheEntry(fingerprint="lock", content=secrets.token_bytes(16))
if await backend.set_if_absent(f"stream:{user_id}", owner, ttl=300):
    try:
        ...
    finally:
        await backend.delete_if_equals(f"stream:{user_id}", owner)
```

- `increment(key, delta=1, ttl=None) -> int`：記憶體後端在鎖內執行讀取—修改—寫入，Redis 執行 Lua 腳本（`EXISTS` + `INCRBY` + `EXPIRE`），Memcached 則使用 `ADD` + `INCR`/`DECR`（Memcached 的計數器最低停在 0）。計數器可透過 `get()` 讀到，形式為 fingerprint 為 `COUNTER_FINGERPRINT`、內容為十進位數值的 `CacheEntry`，因此 `delete`／`clear*` 與監控路由都會把它當成一般項目處理。對存放其他內容的鍵執行 increment，在每個後端上都會拋出 `CacheXError`，即使是本文剛好是數字的快取回應也一樣。以 `set(key, counter_entry(n))` 寫入的計數器在每個後端上都可以 increment，唯一的例外是 Memcached 的計數器沒有正負號：在它上面 `n` 必須介於 0 到 2**64 - 1 之間，對負數的計數器執行 increment 會拋出 `CacheXError`。`delta` 必須是 signed 64 位元範圍內的 `int`，否則會在存取後端之前拋出 `TypeError` 或 `ValueError`。
- `get_and_delete(key) -> CacheEntry | None`：記憶體後端在鎖內 pop，Redis 使用 `GETDEL`（伺服器 6.2 以上），Memcached 使用 `GETS` + `exptime=-1` 的 `CAS` 寫入（若中間有其他寫入者替換了值則會重試；連續 16 次都被替換時會拋出 `CacheXError`，而不是當成鍵不存在）。`StateManager.consume_state`、`StateManager.delete_state`、`CacheManager.delete` 與 `invalidate()` 都建立在它之上。
- `set_if_absent(key, value, ttl=None) -> bool`：只在 `key` 不存在時儲存 `value`（已過期的鍵視為不存在），並回報是否有寫入。記憶體後端在鎖內檢查，Redis 使用 `SET NX EX`，Memcached 使用 `ADD`。
- `delete_if_equals(key, expected) -> bool`：只在 `key` 仍存放 `expected` 時才移除它，因此項目已過期的持有者無法釋放已被他人取得的鎖。請在你儲存的項目中放入唯一的權杖，並以同一個項目釋放。記憶體後端在鎖內比較，Redis 透過 Lua 腳本刪除，並在腳本中重新檢查先前比較過的值，Memcached 則使用 `GETS` + 一個讓項目立即過期的 `CAS` 寫入（傳統協定的 `DELETE` 不接受 CAS 權杖）。
- `expire_if_equals(key, expected, ttl) -> bool`：只在 `key` 仍存放 `expected` 時，才把它的 TTL 更新為 `ttl` 秒，因此長時間執行的鎖持有者可以續約租期，而不會在鎖已過期時動到別人的鎖。記憶體後端在鎖內更新，Redis 先在 Python 中比較，再執行 Lua 腳本（`GET` 比較 + `EXPIRE`），Memcached 則使用 `GETS` + 以新 exptime 寫回相同位元組的 `CAS`（`TOUCH` 不接受 CAS 權杖）。

這五個方法在 `BaseCacheBackend` 上都有非原子性的後備實作，因此只實作抽象方法的第三方後端仍可正常運作；覆寫它們才能得到真正的原子性。

完整可執行範例（英文）：[`examples/rate_limit.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/rate_limit.py)。

## TTL 值 {#ttl-values}

每個 `ttl` 參數（`set`、`set_if_absent`、`increment`，以及建立在它們之上的 `CacheManager` 與 `StateManager` 方法和預設值）只能是 `None`（表示項目永不過期），或介於 1 到 `MAX_TTL`（2**31 - 1，約 68 年）之間的 `int` 秒數。這些檢查都在存取後端之前進行：

- 零、負值與更大的值會拋出 `ValueError`。底層儲存對 `0` 的解讀各不相同：Memcached 把 exptime `0` 視為「永不過期」，Redis 拒絕 `EX 0`，而行程內的 dict 則會立即讓項目過期。
- `float`、`bool` 或其他型別會拋出 `TypeError`。float 過去只在記憶體後端上有效，而 `True` 會被當成一秒。`timedelta` 請以 `int(td.total_seconds())` 轉換。
- Memcached 無法儲存 2038-01-19 之後的過期時間（它的 exptime 是 signed 32 位元時間戳），因此 Memcached 後端遇到超過這個時間點的 `ttl` 會拋出 `ValueError`，而不是接受一筆會立即被丟棄的寫入。

第三方後端應在其 `set` 中呼叫 `fastapi_cachex.backends.base.validate_ttl(ttl)`，並在 `increment` 中呼叫 `validate_delta(delta)`，以遵循相同規則。（`@cache(ttl=0)` 是另一回事：它會送出 `max-age=0`，且絕不會把 `0` 傳給後端；見 [HTTP 快取](HTTP_CACHING.md)。）

各後端如何儲存項目，請見[快取流程](CACHE_FLOW.md#backend-storage-formats)；類別本身請見 [API 參考](https://fastapi-cachex.readthedocs.io/en/latest/api/backends/)（英文）。
