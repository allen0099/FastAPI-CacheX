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

# 一次後端呼叫讀寫多個鍵。沒有快取的鍵不會出現在結果中。
await manager.set_many({"user:1": {"name": "Ann"}, "user:2": {"name": "Ben"}}, ttl=300)
users = await manager.get_many(["user:1", "user:2", "user:3"])  # 沒有 "user:3"

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

`@cached` 對一般函式做的事與 `get_or_set()` 相同，以函式的引數作為鍵，因此載入資料或呼叫其他服務的函式只要寫一次，在任何地方呼叫都會被快取。以下取自 [`examples/app_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/app_cache.py)（程式碼註解為英文）：

<!-- fmt:off -->
```python
--8<-- "examples/app_cache.py:cached"
```
<!-- fmt:on -->

- 被裝飾的函式一律要 `await`，即使它原本是 `def`（同步函式會在事件迴圈上執行，和 `get_or_set()` 的 factory 一樣）。它的結果會經過 manager 的 [JSON 往返](#json-round-trip)，接著會驗證成函式的回傳型別註記（即[型別化讀取](#typed-reads)），第一次呼叫也一樣；`type_=None` 則回傳 JSON 形式。
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
- `set()` 可儲存 JSON 能編碼的值，以及 pydantic 能轉成 JSON 的值（模型、dataclass、`datetime`、`Decimal`、`UUID`、set；見 [JSON 往返](#json-round-trip)）。其他的值會拋出 `TypeError`。
- `get_many(keys, type_=None)` 與 `set_many(mapping, ttl=None)` 以一次後端呼叫讀取、寫入多個鍵，前綴、編碼與 `ttl` 的規則都和 `get()`、`set()` 相同。`get_many()` 回傳一個 dict，內含找到的鍵。不存在、已過期或無法解碼的鍵（指定 `type_` 時，還包括值不符合該型別的鍵）不會出現在其中，因此存入的 `None` 會在 dict 裡，未命中則不會；重複傳入的鍵只讀取一次。`set_many()` 會先編碼所有的值才開始寫入，因此只要有一個值無法編碼就會拋出 `TypeError`，而且不會儲存任何內容。寫入本身在記憶體與 Redis 後端上是全有或全無；在 Memcached 上每個鍵各自儲存，因此發生錯誤時可能只存入了這一批的一部分（見[批次讀寫](BACKENDS.md#batch-reads-and-writes)）。
- `increment(key, delta=1, ttl=None)` 以原子操作累加計數器並回傳新的值，適合配額與速率限制。它是後端的 `increment()` 加上 manager 的前綴：不存在的鍵視為 0，`ttl` 在建立計數器時套用，`get()` 會把計數器讀成 `int`。以 `set()` 寫入值的鍵不是計數器，會拋出 `CacheXError`。
- `get_or_set()` 預設使用 cache stampede 保護（`lock=True`，可針對單次呼叫或以 `CacheManager(lock=...)` 全域設定），避免多次並行未命中時同時執行 `factory`，若等待超時則具備直接計算的優雅降級回退。分散式鎖的鍵名格式為 `lock:<prefix><key>`（預設為 `lock:cache:user:42`）。
- `get_or_set()` 在未命中與命中時都回傳經 JSON 解碼後的值（見 [JSON 往返](#json-round-trip)），因此兩條路徑的結果相同。
- `add()` 只在鍵尚未被占用時寫入值，並回傳是否有寫入。檢查與寫入是同一個後端原子操作（`set_if_absent`），因此適合「每個鍵只做一次」的工作，例如 webhook 或電子郵件的去重。已過期的鍵視為未被占用；存放無法解碼之值的鍵則不算，即使 `get()` 會把它當成未命中。
- 鍵預設位於獨立、以 `cache:` 為前綴的命名空間，與 HTTP 路由快取及鎖分開，因此 `clear()`／`clear_prefix()` 絕不會動到無關的快取項目。
- 前綴是以單純的字串前綴比對。因此 `key_prefix="cache:"` 的 manager 也會清除 `key_prefix="cache:users:"` 的 manager 的項目；而空的 `key_prefix` 會讓 `clear()` 移除後端中的所有內容，包括 HTTP 回應與鎖。請讓每個 manager 的前綴都不以另一個 manager 的前綴開頭。
- `clear_pattern(pattern)` 只把 `pattern` 當成 glob；`key_prefix` 一律照字面比對。前綴不含 glob 特殊字元（`*`、`?`、`[`、`]`、`\`）時，會把 `key_prefix + pattern` 交給後端的 `clear_pattern()`（Redis `SCAN MATCH`），`pattern` 採用後端的 glob 語法。前綴含有這些字元時（例如 `cache[1]:`），無法把它當成 glob 傳給後端，因此 `clear_pattern()` 會以 `get_all_keys()` 列出所有鍵，保留以該前綴開頭、且其餘部分以 `fnmatch.fnmatchcase` 符合 `pattern` 的鍵，再以 `delete_many()` 刪除。這在 Redis 上較慢，而且此時 `pattern` 採用 fnmatch 語法而非 Redis glob：區分大小寫、不支援反斜線跳脫，否定用 `[!a]` 而非 `[^a]`。以這種前綴建立 `CacheManager` 時會發出 `UserWarning`；請改用不含 `*?[]\` 的前綴以維持快速路徑。
- `AppCache` 依賴項在第一次使用時會建立並註冊一個預設的 `CacheManager`；`CacheManagerProxy.set()` 則可改為註冊你自己的實例。

> [!NOTE]
> `CacheManager.clear()`／`clear_prefix()` 是以後端的 `get_all_keys()` 與 `delete_many()` 實作（在 Redis 上是每批 100 個鍵的 `DEL`）。由於 Memcached 不支援列舉鍵（見[後端](BACKENDS.md#memcached)），這些方法以及 `CacheManager.clear_pattern()` 在 Memcached 後端上不會有任何作用，只會回傳 0 並發出 `RuntimeWarning`；`get()`／`set()`／`add()`／`delete()`／`has()` 則照常運作。若需要大量清除，請使用 Redis 或記憶體後端。不要在 Memcached 上改用後端本身的 `clear()`：`MemcachedBackend.clear()` 會發出 `flush_all`，清空整台伺服器，包括 HTTP 回應、鎖以及其他應用程式的鍵。

### 用 tag 讓一組值失效 {#group-invalidation-on-memcached}

寫入值時可以附上一個或多個 tag，之後不需要知道鍵，就能一次讓某個 tag 底下的所有值失效：

```python
from fastapi_cachex import AppCache, cached, invalidate_tags


@app.get("/users/{user_id}")
async def read_user(user_id: int, cache: AppCache):
    return await cache.get_or_set(
        f"user:{user_id}",
        lambda: load_user(user_id),
        ttl=300,
        tags=["users", f"user:{user_id}"],
    )


@cached(ttl=300, tags=["users"])
async def count_users() -> int: ...


@app.post("/users/import")
async def import_users():
    await run_import()
    await invalidate_tags("users")  # 所有以 "users" 寫入的值
```

`set()`、`get_or_set()` 與 `@cached` 都接受 `tags=`；`@cached` 還可以傳入一個函式，它會原樣收到該次呼叫傳入的引數並回傳 tag（`tags=lambda user_id: [f"user:{user_id}"]`）。`set_many()` 與 `add()` 不接受 tag。`invalidate_tags()` 也是 `CacheManager` 的方法。這些 tag 與同一個後端上的 [`@cache(tags=...)`](HTTP_CACHING.md#by-tag) 路由共用，因此一次呼叫就能同時讓快取的回應與快取的值失效。

運作方式，以及由此而來的特性：

- 每個 tag 有一個版本，存放在後端的 `tag:<名稱>` 底下。值在寫入時會記下它各個 tag 的版本，只要其中任何一個變了，讀取時就視為未命中。過程中不掃描也不列舉任何鍵，所以每個後端都能用，包含 Memcached。
- 讀取帶有 tag 的值會多一次批次的後端讀取（`get_many`），寫入也一樣；某個 tag 底下的第一次寫入還會存下它的版本。沒有 tag 的值沒有任何額外成本。
- 對已失效的值，`has()` 會回傳 false，`add()` 會取代它。
- 只有以 `tags=` 寫入的值才受影響。在加上 tag 之前寫入的值，或以其他名稱寫入的值，會持續被提供直到 TTL 到期：加上或改名 tag 之後，請刪除這些鍵，或等一個 TTL 週期。（`@cache` 路由會自行處理這種情況；單純的 `get()` 無從得知某個鍵應該有哪些 tag，而 `get_or_set()` 與 `@cached` 也是透過它讀取的。）
- 版本是在有值以該 tag 寫入時才建立的，查詢時不會建立：沒有寫入任何東西的呼叫不會留下任何鍵。
- 失效的值不會被刪除，而是隨 TTL 消失，所以請為帶有 tag 的值設定 TTL。
- 使用 `get_or_set()` 與 `@cached` 時，版本是在計算值之前讀取的，所以若資料在計算期間改變，算出來的值在失效之後不會被提供。
- 版本從設定起保留 30 天，所以「每個使用者一個」或「每筆資料一個」的 tag 不會永久留下鍵。版本到期後，以該 tag 寫入的值會視為未命中，下一次寫入會開始新的版本：帶有 tag 的值最多存活 30 天，不論它自己的 TTL 多長。
- 版本提早遺失時也一樣（Memcached 在記憶體不足時淘汰項目、`flush_all`、重新啟動，或設了 `max_entries` 的 `MemoryBackend`：`tag:` 鍵也算項目，可能被淘汰）。版本遺失只會多一次未命中，不會提供已失效的值。
- 0.6.1 之前的版本不認得 tag，會持續提供帶有 tag 的值直到 TTL 到期。請先把所有 worker 都升級，再依賴 `invalidate_tags()`。

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

## 過期的值 {#stale-values}

值的 `ttl` 到期時，每個呼叫者都要等 `factory`；上游故障時，即使前一刻還知道最後一個正常的值，也會變成錯誤。`stale_ttl` 會把值多保留這麼久，並在這段期間繼續使用它：

```python
rates = await cache.get_or_set("rates", load_rates, ttl=60, stale_ttl=300)


@cached(ttl=60, stale_ttl=300)
async def load_rates() -> dict[str, float]: ...
```

值會保留 `ttl + stale_ttl`（此例為 360 秒）。前 `ttl` 秒是一般的命中。之後的 `stale_ttl` 秒內：

- 第一個發現它過期的呼叫者會執行 `factory` 並儲存結果，新的值再保鮮 `ttl` 秒。
- 在它執行期間抵達的呼叫者會立刻拿到過期的值，不必等待。
- 若 `factory` 拋出例外，該呼叫者也會拿到過期的值，並記錄一行警告。下一個呼叫者會再試一次。

超過 `ttl + stale_ttl` 後值就不存在了，`factory` 失敗時會照常拋出例外。

- **沒有背景工作。** 更新是在發現值過期的那次呼叫裡執行的，所以只有那一個呼叫者要等 `factory`，而且請求結束後不會有任何東西還在執行。
- **同一時間只有一個更新**：每個行程中的每個鍵。使用 `lock=True`（預設）時，共用後端的所有行程合計也只有一個：其他行程會回傳過期的值，而不是等待鎖。
- **需要 `ttl`**：在呼叫時傳入，或設定管理器的 `default_ttl`；否則會拋出 `ValueError`。
- **只有帶 `stale_ttl` 的呼叫會拿到過期的值。** `get()`、`get_many()` 與 `has()` 把它視為未命中，`add()` 會取代它，不帶 `stale_ttl` 的 `get_or_set()` 則和一般未命中一樣執行 `factory`。因此直接讀取這個鍵的程式永遠不會看到比 `ttl` 更舊的值。
- **`factory` 的任何例外都算**，包含值不符合 `type_` 的情況。請留意 `Refreshing a stale value failed` 這則警告。
- tag 被失效的值是未命中，不是過期的值。
- 值何時過期是以牆上時鐘的時間與值一起儲存的，並以讀取它的行程的時鐘比較；時鐘相差幾秒的伺服器，判斷也會相差那麼多。0.6.3 之前的版本不認得這個時間：滾動升級期間，舊版本會在整個 `ttl + stale_ttl` 期間回傳這個值。
- 拿到過期值的呼叫者會回報為 `stale` [事件](HTTP_CACHING.md#counting-hits-and-misses)；執行更新的呼叫者則回報為 `miss`。

## JSON 往返 {#json-round-trip}

值以 JSON 儲存，讀取時以 `json.loads` 解碼，因此取回的是 JSON 解碼後的形式，而不是你存入的物件。`json.dumps` 無法編碼的值會先依 pydantic 的寫法轉換（`pydantic_core.to_jsonable_python`）；0.5.5 之前則會拋出 `TypeError`：

| 存入 | 讀回 |
|---|---|
| `dict`、`list`、`str`、`int`、`float`、`bool`、`None` | 不變 |
| `tuple` | `list`：`(1, 2)` → `[1, 2]` |
| 鍵為 `int`／`float`／`bool`／`None` 的 `dict` | 字串鍵：`{1: "a"}` → `{"1": "a"}` |
| pydantic 模型、dataclass | `dict`，即 `model_dump(mode="json")` 的結果 |
| `datetime`、`date`、`UUID`、`Decimal` | `str`：ISO 8601、`"1.50"` |
| `set`、`frozenset` | `list` |
| `Enum`、`Path`、`timedelta` 以及 pydantic 能序列化的其他型別 | 它們的 JSON 形式：enum 的值、`"/tmp/x"`、`"PT1M30S"` |
| `bytes`、generator 或其他 iterator、`SecretStr`（作為模型的欄位時也一樣）、你自己的類別 | `TypeError`，不會儲存任何內容 |

`get_or_set()` 在未命中時也套用同樣的規則：它將 `factory` 的值編碼一次、儲存這些位元組，並回傳其解碼結果，因此第一次呼叫的回傳值與之後命中時完全相同。無法編碼的值要等到 `factory` 執行後才會拋出 `TypeError`，因為在那之前這個值還不存在。

### 型別化讀取 {#typed-reads}

若想取回物件而不是它的 JSON 形式，請指定型別。`get()`、`get_or_set()` 與 `@cached` 都接受 `type_=`（pydantic 的 `TypeAdapter` 接受的任何型別），並把解碼後的值驗證成該型別：

```python
from datetime import datetime

from pydantic import BaseModel

from fastapi_cachex import AppCache


class User(BaseModel):
    id: int
    joined: datetime


async def read_user(user_id: int, cache: AppCache) -> User:
    # 以 JSON 儲存；命中與未命中時都會驗證回 User。
    return await cache.get_or_set(
        f"user:{user_id}", lambda: load_user(user_id), ttl=300, type_=User
    )


await cache.set("user:1", user)  # 模型會以 model_dump(mode="json") 的形式儲存
await cache.get("user:1")  # {"id": 1, "joined": "2026-01-02T03:04:05Z"}
await cache.get("user:1", type_=User)  # User(id=1, joined=datetime(...))
await cache.get("users", type_=list[User])
```

- 存入的值若不符合型別（例如在新增欄位之前寫入的），會被當成無法解碼的值：`get()` 記錄一筆警告並回傳預設值，`get_or_set()` 則重新執行 `factory` 並覆寫它。
- 若 `factory` 回傳的值不符合 `type_`，`get_or_set()` 會拋出 pydantic 的 `ValidationError`。
- 未傳入 `type_` 時，`@cached` 會使用函式的回傳型別註記，因此 `async def load_user(...) -> User` 會回傳 `User`。`type_=None` 則回傳 JSON 形式；沒有註記的函式，以及註記是 JSON 形式本來就具有之型別（`dict[str, Any]`、`list[int]`）的函式也是。0.6.0 之前不會讀取註記；見[遷移至 0.6.0](MIGRATING_0_6.md#cached-return-annotation)。
- 存入的 `None` 不符合 `type_=User`。值可能不存在時，請明確寫出：`type_=User | None`。
- 帶有 `SecretStr` 或 `SecretBytes` 欄位的模型或 dataclass（不論巢狀多深）會被拒絕並拋出 `TypeError`。pydantic 會把該欄位寫成 `"**********"`，型別化讀取之後就會交回一個機密值是這串遮罩的模型。請排除該欄位（`user.model_dump(mode="json", exclude={"password"})`），或改為快取你自己組出的 `dict`。
- pydantic 的 `ValidationError` 會引用它拒絕的值，因此 `factory` 結果不符時拋出的例外可能把該值寫進你的 log。
- 每次讀取都會執行驗證。對於經常讀取的大型值，選擇模型而非單純的 `dict` 之前請先量測。

完整的方法清單請見 [API 參考](https://fastapi-cachex.readthedocs.io/en/stable/api/cache-manager/)（英文）。
