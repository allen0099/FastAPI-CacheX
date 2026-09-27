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


# 也可以直接建立實例，例如在請求之外使用：
manager = CacheManager(key_prefix="myapp:", default_ttl=60)
await manager.set("user:42", {"name": "Alice"})
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

## 行為 {#behavior}

- `get()` 在快取未命中時回傳 `None`（或你提供的 `default=`），遇到不存在或損毀的項目也絕不會拋出例外。
- `set()` 遇到無法 JSON 序列化的值時，會讓 `TypeError` 直接往外拋出。
- `get_or_set()` 不提供 cache stampede 保護：同一個鍵同時發生多次未命中時，每一次都會執行 `factory`。
- `get_or_set()` 在未命中與命中時都回傳經 JSON 解碼後的值（見 [JSON 往返](#json-round-trip)），因此兩條路徑的結果相同。
- `add()` 只在鍵尚未被占用時寫入值，並回傳是否有寫入。檢查與寫入是同一個後端原子操作（`set_if_absent`），因此適合「每個鍵只做一次」的工作，例如 webhook 或電子郵件的去重。已過期的鍵視為未被占用；存放無法解碼之值的鍵則不算，即使 `get()` 會把它當成未命中。
- 鍵預設位於獨立、以 `cache:` 為前綴的命名空間，與 HTTP 路由快取及 OAuth state 分開，因此 `clear()`／`clear_prefix()` 絕不會動到無關的快取項目。
- 前綴是以單純的字串前綴比對。因此 `key_prefix="cache:"` 的 manager 也會清除 `key_prefix="cache:users:"` 的 manager 的項目；而空的 `key_prefix` 會讓 `clear()` 移除後端中的所有內容，包括 HTTP 回應、鎖、OAuth state 與 Session。請讓每個 manager 的前綴都不以另一個 manager 的前綴開頭。
- `clear_pattern(pattern)` 只把 `pattern` 當成 glob；`key_prefix` 一律照字面比對。前綴不含 glob 特殊字元（`*`、`?`、`[`、`]`、`\`）時，會把 `key_prefix + pattern` 交給後端的 `clear_pattern()`（Redis `SCAN MATCH`），`pattern` 採用後端的 glob 語法。前綴含有這些字元時（例如 `cache[1]:`），無法把它當成 glob 傳給後端，因此 `clear_pattern()` 會以 `get_all_keys()` 列出所有鍵，保留以該前綴開頭、且其餘部分以 `fnmatch.fnmatchcase` 符合 `pattern` 的鍵，再以 `delete_many()` 刪除。這在 Redis 上較慢，而且此時 `pattern` 採用 fnmatch 語法而非 Redis glob：區分大小寫、不支援反斜線跳脫，否定用 `[!a]` 而非 `[^a]`。以這種前綴建立 `CacheManager` 時會發出 `UserWarning`；請改用不含 `*?[]\` 的前綴以維持快速路徑。
- `AppCache` 依賴項在第一次使用時會建立並註冊一個預設的 `CacheManager`；`CacheManagerProxy.set()` 則可改為註冊你自己的實例。

> [!NOTE]
> `clear()`／`clear_prefix()` 是以後端的 `get_all_keys()` 與 `delete_many()` 實作（在 Redis 上是一次批次 `DEL`）。由於 Memcached 不支援列舉鍵（見[後端](BACKENDS.md#memcached)），這些方法以及 `clear_pattern()` 在 Memcached 後端上不會有任何作用；`get()`／`set()`／`add()`／`delete()`／`has()` 則照常運作。若需要大量清除，請使用 Redis 或記憶體後端。

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
