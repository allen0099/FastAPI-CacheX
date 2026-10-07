# 應用層快取 {#application-cache}

除了透過 `@cache` 快取 HTTP 回應，你也可以用 `CacheManager` 在商業邏輯中直接快取任意可 JSON 序列化的 Python 值。它是一層輕量、具命名空間的包裝，底層使用透過 `BackendProxy` 設定的後端。

```python
from fastapi_cachex import AppCache, CacheManager


@app.get("/expensive")
async def expensive_operation(cache: AppCache):
    result = await cache.get("expensive:result")
    if result is None:
        result = perform_expensive_calculation()
        await cache.set("expensive:result", result, ttl=300)
    return result


# 也可以直接建立實例，例如在請求之外使用，前提是已用 BackendProxy.set(...)
# 設定後端。`lock` 預設為 True
# （見「Cache stampede 保護」）。
manager = CacheManager(key_prefix="myapp:", default_ttl=60)
await manager.set("user:42", {"name": "Alice"})
# 每個 ttl 都接受 int 秒數，或整數秒的 timedelta。
await manager.set("user:43", {"name": "Bob"}, ttl=timedelta(minutes=5))
user = await manager.get("user:42")  # {"name": "Alice"}
await manager.delete("user:42")
await manager.clear_prefix()  # 清除 "myapp:" 底下的所有項目

# 未命中時才計算：只有在鍵不存在、已過期或無法解碼時才會執行 `factory`。
# 它可以是同步或非同步函式。
profile = await manager.get_or_set("user:42", lambda: load_user(42), ttl=300)

# 只在鍵尚未被占用時寫入：同時呼叫的呼叫者中恰好只有一個會得到 True。
if await manager.add(f"webhook:{event_id}", True, ttl=86400):
    await deliver_webhook(event_id)

# 在此 manager 的命名空間內做萬用字元（glob）比對。只有 pattern 是 glob，
# 前綴一律照字面比對。前綴不含 *?[]\ 時，會使用後端原生的模式比對支援
# （Redis SCAN），而不是列舉所有鍵。
await manager.clear_pattern("user:*")  # 比對 "myapp:user:*"
```

完整可執行範例（英文）：[`examples/app_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/app_cache.py)。

## 快取一個函式 {#caching-a-function}

`@cached` 對一般函式做的事與 `get_or_set()` 相同，以函式的引數作為鍵，因此載入資料或呼叫其他服務的函式只要寫一次，在任何地方呼叫都會被快取：

<!-- fmt:off -->
```python
--8<-- "examples/app_cache.py:cached"
```
<!-- fmt:on -->

- 被裝飾的函式一律要 `await`，即使它原本是 `def`（同步函式會在事件迴圈上執行，和 `get_or_set()` 的 factory 一樣）。它的結果會經過 manager 的 [JSON 往返](#json-round-trip)，因此必須可 JSON 序列化，拿回來的也是 JSON 解碼後的形式，第一次呼叫也一樣。
- `ttl` 接受秒數或 `timedelta`；未設定時套用 manager 的 `default_ttl`。`manager=` 指定要透過哪個 `CacheManager` 儲存；未設定時使用應用程式的那一個（也就是 `AppCache` 回傳的），並在每次呼叫時解析，因此在啟動時以 `CacheManagerProxy.set()` 註冊的 manager 也會被採用。`lock=False` 讓這個函式略過 manager 的 [cache stampede 保護](#stampede-protection)。
- 未設定 `key=` 時，鍵是 `module.qualname:` 加上引數的 SHA-256；引數會先依函式簽章綁定並套用預設值，因此 `load(1)`、`load(user_id=1)` 與 `load(1, locale="en")` 共用同一個項目。引數以 JSON 雜湊，因此必須可 JSON 序列化；帶有無法序列化之引數的呼叫會拋出 `CacheXError`。`key="user:{user_id}"` 是以引數名稱填入的 `str.format` 樣板（不含欄位的字串就是固定的鍵），`key=lambda self, user_id: f"user:{user_id}"` 則會以引數呼叫：方法的 `self` 無法雜湊，或引數是模型時，請用其中一種。manager 的 `key_prefix` 會加在前面，和它儲存的每個鍵一樣。
- `fn.cache_key(*args, **kwargs)` 是該次呼叫使用的鍵（不含前綴），`await fn.invalidate(*args, **kwargs)` 則丟棄它的值，並回傳原本是否有快取。在方法上，`obj.load.invalidate(1)` 會和呼叫時一樣綁定 `obj`。

## 行為 {#behavior}

- `get()` 在快取未命中時回傳 `None`（或你提供的 `default=`），遇到不存在或損毀的項目也絕不會拋出例外。存入的 `None`（JSON 的 `null`）讀回來也是 `None`，因此光靠 `get()` 無法分辨「快取了沒有結果」與「沒有快取」，用這種方式快取否定結果的程式碼每次呼叫都會重新計算。要分辨兩者，可以傳入一個哨兵值作為預設值、先用 `has()` 檢查，或改用 `get_or_set()`，它只在真正未命中時執行 `factory`，否則回傳存入的 `None`：

  ```python
  missing = object()
  value = await cache.get("user:42", default=missing)
  if value is missing:
      ...  # 沒有快取；快取的 None 會以 None 回傳
  ```
- `set()` 遇到無法 JSON 序列化的值時，會讓 `TypeError` 直接往外拋出。
- `get_or_set()` 預設使用 cache stampede 保護（`lock=True`，可針對單次呼叫或以 `CacheManager(lock=...)` 全域設定），避免多次並行未命中時同時執行 `factory`，若等待超時則具備直接計算的優雅降級回退。分散式鎖的鍵名格式為 `lock:<prefix><key>`（預設為 `lock:cache:user:42`）。
- `get_or_set()` 在未命中與命中時都回傳經 JSON 解碼後的值（見 [JSON 往返](#json-round-trip)），因此兩條路徑的結果相同。
- `add()` 只在鍵尚未被占用時寫入值，並回傳是否有寫入。檢查與寫入是同一個後端原子操作（`set_if_absent`），因此適合「每個鍵只做一次」的工作，例如 webhook 或電子郵件的去重。已過期的鍵視為未被占用；存放無法解碼之值的鍵則不算，即使 `get()` 會把它當成未命中。
- 鍵預設位於獨立、以 `cache:` 為前綴的命名空間，與 HTTP 路由快取及 OAuth state 分開，因此 `clear()`／`clear_prefix()` 絕不會動到無關的快取項目。
- 前綴是以單純的字串前綴比對。因此 `key_prefix="cache:"` 的 manager 也會清除 `key_prefix="cache:users:"` 的 manager 的項目；而空的 `key_prefix` 會讓 `clear()` 移除後端中的所有內容，包括 HTTP 回應、鎖、OAuth state 與 Session。請讓每個 manager 的前綴都不以另一個 manager 的前綴開頭。
- `clear_pattern(pattern)` 只把 `pattern` 當成 glob；`key_prefix` 一律照字面比對。前綴不含 glob 特殊字元（`*`、`?`、`[`、`]`、`\`）時，會把 `key_prefix + pattern` 交給後端的 `clear_pattern()`（Redis `SCAN MATCH`），`pattern` 採用後端的 glob 語法。前綴含有這些字元時（例如 `cache[1]:`），無法把它當成 glob 傳給後端，因此 `clear_pattern()` 會以 `get_all_keys()` 列出所有鍵，保留以該前綴開頭、且其餘部分以 `fnmatch.fnmatchcase` 符合 `pattern` 的鍵，再以 `delete_many()` 刪除。這在 Redis 上較慢，而且此時 `pattern` 採用 fnmatch 語法而非 Redis glob：區分大小寫、不支援反斜線跳脫，否定用 `[!a]` 而非 `[^a]`。以這種前綴建立 `CacheManager` 時會發出 `UserWarning`；請改用不含 `*?[]\` 的前綴以維持快速路徑。
- `AppCache` 依賴項在第一次使用時會建立並註冊一個預設的 `CacheManager`；`CacheManagerProxy.set()` 則可改為註冊你自己的實例。

> [!NOTE]
> `CacheManager.clear()`／`clear_prefix()` 是以後端的 `get_all_keys()` 與 `delete_many()` 實作（在 Redis 上是每批 100 個鍵的 `DEL`）。由於 Memcached 不支援列舉鍵（見[後端](BACKENDS.md#memcached)），這些方法以及 `CacheManager.clear_pattern()` 在 Memcached 後端上不會有任何作用，只會回傳 0 並發出 `RuntimeWarning`；`get()`／`set()`／`add()`／`delete()`／`has()` 則照常運作。若需要大量清除，請使用 Redis 或記憶體後端。不要在 Memcached 上改用後端本身的 `clear()`：`MemcachedBackend.clear()` 會發出 `flush_all`，清空整台伺服器，包括 HTTP 回應、Session、鎖以及其他應用程式的鍵。

## Cache stampede 保護 {#stampede-protection}

當 `factory` 的運算成本很高（例如慢速資料庫查詢、受速率限制的外部 API）且該鍵又是熱門鍵時，快取過期會導致多個請求同時重新計算。以 `CacheLock` 實作的分散式 cache stampede 保護預設開啟；你可以在單次呼叫或 manager 全域調整或關閉它（`@cache` 路由請見[同時發生的未命中](HTTP_CACHING.md#concurrent-misses)）：

```python
# 單次呼叫保護：
profile = await manager.get_or_set(
    "user:42",
    lambda: load_user(42),
    ttl=300,
    lock=True,  # 預設：沿用 manager 的 lock，未另行設定時為 True
    lock_ttl=30,  # factory 執行的租約上限（秒，預設：60）
    wait_timeout=10,  # 呼叫者等待的延遲預算（秒，預設：None，持鎖期間持續等待）
    raise_on_timeout=False,  # True 拋出 LockTimeoutError，False 回退至執行 factory（預設：False）
)

# 或 manager 全域預設：
manager = CacheManager(lock=False)  # 每次並行未命中都各自計算
```

1. **未命中**：未命中時，呼叫者嘗試使用 `CacheLock` 進行非阻塞式取鎖，鎖鍵名稱為 `lock:<prefix><key>`（預設為 `lock:cache:user:42`）。
2. **勝出者**：取鎖成功的勝出者會再次檢查快取、執行 `factory`、將值存入後端並釋放鎖。
3. **等待者**：其他呼叫者以指數退避（基準 50ms、倍率 1.5 倍、上限 500ms）與隨機抖動（±10%）輪詢快取，直到值出現為止。未指定 `wait_timeout` 時，呼叫者受限於持鎖者的 `lock_ttl` 期間持續等待，不會有固定的期限。
4. **接手**：若勝出者失敗或其鎖已過期，等待中的呼叫者會接手取得鎖、重新檢查快取並在需要時執行計算。
5. **可重新進入（Re-entrancy）**：同一工作（task）內對同一個鍵遞迴呼叫 `get_or_set()` 會自動跳過取鎖，避免自我死鎖。
6. **逾時**：當明確指定的 `wait_timeout` 到期時，`raise_on_timeout=False` 會記錄警告並回退為直接執行 factory 計算（優雅降級），而 `raise_on_timeout=True` 則拋出 `LockTimeoutError`。

請確保 `lock_ttl` 超過 `factory` 的預期執行時間。若 `factory` 執行時間超過 `lock_ttl`，鎖會在執行途中過期，導致等待中的呼叫者發起第二次計算。

### 成本 {#cost}

0.4.0 起 cache stampede 保護預設開啟（[#280](https://github.com/allen0099/FastAPI-CacheX/issues/280)），`AppCache` 自動建立的 manager 也一樣。它會讓未命中時多出後端往返；命中時兩者都只有一次 `GET`。以下以 Redis 與 Memcached 計算，每一步都是一次網路往返：

| 路徑 | `lock=False` | `lock=True` |
|------|--------------|-------------|
| 命中 | 1：`GET` | 1：`GET` |
| 未命中，沒有其他呼叫者 | 2：`GET`、`SET` | 6：`GET`、取鎖的 `SET NX`（Memcached 為 `add`）、再次檢查的 `GET`、`SET`，以及釋放鎖；釋放時會先讀取鎖，只有鎖仍持有此呼叫者的權杖時才刪除（2） |
| 每個等待中的呼叫者 | 無：自己執行 `factory` | 開始時 2 次（`GET`、嘗試取鎖），之後每次輪詢 2 次（`GET`、嘗試取鎖），直到值出現 |

等待者依上述退避輪詢：第一秒約五次，之後每 500 ms 一次，因此在 `factory` 執行期間，每個等待者每秒約多出四次後端操作。記憶體後端在行程內執行相同的步驟。對重複計算比額外往返更便宜的鍵，請在單次呼叫或 `CacheManager(...)` 傳入 `lock=False`；使用 `AppCache` 時，請以 `CacheManagerProxy.set(CacheManager(lock=False))` 註冊自己的 manager。

## JSON 往返 {#json-round-trip}

值以 JSON 儲存（`json.dumps` 的預設設定），讀取時以 `json.loads` 解碼，因此取回的是 JSON 解碼後的形式，而不是你存入的物件：

| 存入 | 讀回 |
|---|---|
| `dict`、`list`、`str`、`int`、`float`、`bool`、`None` | 不變 |
| `tuple` | `list`：`(1, 2)` → `[1, 2]` |
| 鍵為 `int`／`float`／`bool`／`None` 的 `dict` | 字串鍵：`{1: "a"}` → `{"1": "a"}` |
| `datetime`、`Decimal`、`UUID`、`set`、pydantic 模型…… | `TypeError`，不會儲存任何內容 |

`get_or_set()` 在未命中時也套用同樣的規則：它將 `factory` 的值編碼一次、儲存這些位元組，並回傳其解碼結果，因此第一次呼叫的回傳值與之後命中時完全相同。JSON 無法編碼的值要等到 `factory` 執行後才會拋出 `TypeError`，因為在那之前這個值還不存在；請先轉換，例如 `lambda: model.model_dump(mode="json")` 或 `lambda: when.isoformat()`。

完整的方法清單請見 [API 參考](https://fastapi-cachex.readthedocs.io/en/latest/api/cache-manager/)（英文）。
