# FastAPI-CacheX 快取流程 {#fastapi-cachex-cache-flow}

本文件詳細說明 FastAPI-CacheX 如何將快取邏輯套用到 HTTP 請求上。除非另有說明，這裡描述的所有行為都位於 [`fastapi_cachex/cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/cache.py)。

## 整體流程 {#overall-flow}

```
HTTP 請求抵達
    ↓
@cache 裝飾器攔截請求（只有 GET 會經過快取；其他所有
方法直接執行 handler，也不會帶上 Cache-Control 標頭）
    ↓
建立快取鍵：method|||host|||path|||query_params
    ↓
no-store？ ── 是 → 執行 handler，既不讀取也不寫入快取，
    │              回應帶上 Cache-Control: no-store
    ↓ 否
private？ ── 是 → 執行 handler；比對 If-None-Match 決定回傳 304 或 200
    │              （共用後端既不讀取也不寫入）
    ↓ 否
讀取後端項目
    ↓
請求帶有 If-None-Match？
    ├─ 且為 no-cache → 先執行 handler 計算目前的 ETag；相符 → 304
    ├─ 其他情況      → 與快取項目的 ETag 比對；相符 → 304
    └─ 不相符／沒有此標頭 → 繼續
    ↓
快取項目存在、已設定 ttl，且未啟用 no-cache？
    ├─ 是 → 以快取內容回應（包含儲存的狀態碼
    │        與標頭；handler **不會**執行）
    └─ 否 → 執行 handler
              ├─ 非 2xx（或 206）→ 原樣回傳且**不寫入**
              │                    （不會覆寫既有的正常項目）
              ├─ 串流／檔案回應 → 無法計算 ETag；原樣回傳，不寫入
              └─ 一般回應 → 設定 ETag；只有與既有項目的 ETag
                            不同時才寫入後端
    ↓
在回應中附加 Cache-Control（非 2xx 回應回傳時不帶此標頭）
```

## 詳細步驟 {#detailed-steps}

### 1. 請求攔截與快取鍵產生 {#1-request-interception-and-key-generation}

請求抵達時，`@cache` 裝飾器會執行以下步驟：

```python
from fastapi_cachex.types import CACHE_KEY_SEPARATOR  # "|||"

# 快取鍵格式（fastapi_cachex/cache.py 中的 default_key_builder）
cache_key = CACHE_KEY_SEPARATOR.join(
    [request.method, request.headers.get("host", "unknown"), request.url.path, query]
)

# 例如：
# GET|||example.com|||/api/users|||page=1&limit=10
# GET|||api.example.com|||/api/users/123|||
```

分隔符號使用 `|||` 而不是冒號，是因為 host 本身可能包含連接埠（`127.0.0.1:8000`）；若使用冒號，快取鍵就無法可靠地拆分，而 `clear_path()` 需要從快取鍵中取回路徑。

查詢參數依請求送出的順序串接（`str(request.query_params)`），**不會排序**，因此 `?page=1&limit=10` 與 `?limit=10&page=1` 是兩個不同的快取項目。若希望兩者視為同一個，請傳入自訂的 `key_builder` 將查詢字串正規化。

這個快取鍵格式讓每個維度各自獨立快取：

- **方法隔離**：GET 與 POST 不共用快取（而且目前只有 GET 會進入快取流程）
- **Host 隔離**：`example.com` 與 `api.example.com` 分開快取
- **路徑隔離**：每個端點有各自的項目
- **查詢參數隔離**：同一端點上不同的查詢參數分開快取

### 2. Cache-Control 指令 {#2-cache-control-directives}

裝飾器參數同時控制伺服器端的行為，以及送給用戶端的 `Cache-Control` 標頭：

```python
# 改變伺服器端快取的使用方式
@cache(no_cache=True)     # 每次都重新執行 handler（重新驗證）；項目仍會寫入
@cache(no_store=True)     # 永不讀取或寫入快取

# 一般快取行為
@cache(ttl=3600)          # 快取 1 小時（也作為 max-age 的值）
@cache(public=True)       # 允許共用快取
@cache(private=True)      # 僅限私有；永遠不接觸共用後端
@cache(immutable=True)    # 內容永不改變

# 只影響標頭的指令（不會改變伺服器端行為）
@cache(ttl=60, must_revalidate=True)                        # must-revalidate
@cache(ttl=60, stale="revalidate", stale_ttl=30)            # stale-while-revalidate=30
@cache(ttl=60, stale="error", stale_ttl=300)                # stale-if-error=300
```

參數會在套用裝飾器時驗證；若同時設定 `public` 與 `private`，或只提供 `stale`／`stale_ttl` 其中之一，會拋出 `CacheXError`。

標頭值在每個被裝飾的路由上只建立一次：

| 參數 | 送出的 `Cache-Control` |
|------|------------------------|
| `no_store=True` | `no-store`（覆蓋其他所有設定） |
| `no_cache=True` | `no-cache`，若有要求則加上 `must-revalidate`；省略 `public`／`private`／`max-age`／`stale-*`／`immutable` |
| 其他情況 | 依序為：`public` 或 `private`、`max-age=<ttl>`、`must-revalidate`、`stale-while-revalidate=<n>` 或 `stale-if-error=<n>`、`immutable` |

> [!NOTE]
> 沒有設定 `ttl`（或設定 `ttl=0`，此時會送出 `max-age=0`）時，項目仍會寫入（不設過期時間），但永遠不會直接拿來回應：它只用於以 `304` 回應相符的 `If-None-Match`。請設定正數的 `ttl`，讓伺服器重播快取的回應。

> [!WARNING]
> **預設的快取鍵不包含使用者身分**，而且後端由所有 worker 與所有使用者共用。直接在需要驗證的端點上加上 `@cache(ttl=...)`，會把使用者 A 的回應提供給下一個請求相同路徑的使用者 B。
>
> 對於回應內容取決於呼叫者的端點，請擇一處理：
>
> 1. `private=True`：永遠不讀取或寫入共用後端。它仍會送出 `Cache-Control: private`，讓使用者自己的瀏覽器可以快取回應，而且 `If-None-Match` 仍會與新產生的內容比對。
> 2. 包含身分的自訂 `key_builder`：當你確實需要以使用者為單位的伺服器端快取時使用。
>
> 身分請取自可信任的來源（已驗證的權杖 claim、透過依賴注入取得的使用者物件）；不要信任未經檢查的用戶端標頭。

### 3. 快取查詢 {#3-cache-lookup}

以快取鍵查詢後端，後端會回傳 `CacheEntry`（過期的項目由後端自行略過）：

```python
from fastapi_cachex.types import CacheEntry

entry = CacheEntry(
    fingerprint='W/"9f86d081..."',  # ETag，內容的弱驗證器
    content=b'{"data": "response"}',  # 原始回應位元組
    media_type="application/json",
    status_code=200,  # 以原本的狀態碼重播
    headers={"Vary": "Accept-Encoding"},  # 重播時送回的標頭
)
```

TTL 不儲存在 `CacheEntry` 中：過期由後端負責（`MemoryBackend` 將它存在 `CacheItem.expiry`，Redis 使用 `SET ... EX`，Memcached 使用 exptime）。

若尚未以 `BackendProxy.set()` 設定後端，裝飾器會在第一個請求時建立 `MemoryBackend` 並註冊它。

**判斷邏輯**（`cache.py` 的包裝函式，依序執行）：

```python
if request.method != "GET":
    return await handler()                   # 不快取，不帶 Cache-Control

if no_store:
    return await render()                    # 不讀取，不寫入

if private:
    response, etag = await render()          # 共用後端既不讀取也不寫入
    return not_modified(...) if etag_matches(client_etag, etag) else response

entry = await backend.get(cache_key)         # 過期的項目已在此略過

if client_etag and no_cache:
    fresh = await render()                   # no-cache：一律先重新產生
    if etag_matches(client_etag, fresh.etag):
        return not_modified(...)             # 304
elif client_etag and entry and etag_matches(client_etag, entry.fingerprint):
    return not_modified(...)                 # 304，handler 不執行

if entry and not no_cache and ttl is not None:
    return Response(                         # 200，handler 不執行
        content=entry.content,
        status_code=entry.status_code,
        media_type=entry.media_type,
        headers={**(entry.headers or {}), "ETag": entry.fingerprint, ...},
    )

response, body, etag = await render()        # 未命中（若 no-cache 已產生過則直接沿用）
if not is_cacheable_status(response.status_code):
    return response                          # 非 2xx：原樣回傳，不寫入
if etag is None:
    return response                          # 串流／檔案：沒有 ETag，不寫入
if not entry or entry.fingerprint != etag:
    await backend.set(cache_key, CacheEntry(...), ttl=ttl)
return response
```

> [!NOTE]
> 「非 2xx 不寫入」是刻意的設計：暫時性的錯誤不應抹除最後一次正常的快取回應，也不應在之後被當成 200 重播。`206 Partial Content` 同樣不會快取，因為它的內容只對產生它的那個 `Range` 請求有意義。非 2xx 回應也永遠不會以 `304` 回應，且回傳時不帶裝飾器的 `Cache-Control` 標頭（只有 `no_store=True` 會在每個回應加上 `no-store`）。

### 4. ETag 產生與驗證 {#4-etag-generation-and-validation}

ETag 由回應內容計算而來，用於偵測內容是否已改變：

```python
# 產生：MD5，標記為弱驗證器
def _etag_for(body: bytes) -> str:
    return f'W/"{hashlib.md5(body).hexdigest()}"'
```

`If-None-Match` 依 RFC 9110 §8.8.3.2 規定以**弱比較**判斷，因此：

```
If-None-Match: W/"abc"            → 與 "abc" 相符（兩邊的 W/ 前綴都會忽略）
If-None-Match: "abc", W/"def"     → 多個值逐一比對；任一相符 → 304
If-None-Match: *                  → 只要資源存在就相符 → 304
```

304 回應會帶有與 200 相同的 `Cache-Control` 與 `ETag`，以及影響快取行為的標頭 `Vary`、`Content-Location` 與 `Expires`；否則中介快取在重新驗證後會遺失這些欄位（RFC 9110 §15.4.5）。

## 後端儲存格式 {#backend-storage-formats}

### MemoryBackend {#memorybackend}

```python
# dict[str, CacheItem]；CacheItem 包裝 CacheEntry 並記錄其過期時間
{
    "GET|||example.com|||/api/users|||": CacheItem(
        value=CacheEntry(
            fingerprint='W/"abc123"',
            content=b"...",
            media_type="application/json",
            status_code=200,
            headers=None,
        ),
        expiry=1702650600.5,  # epoch 秒數；None 表示永不過期
    ),
}

# 特性：
# - 儲存在行程記憶體中，不在行程之間共用
# - 背景清理任務每 cleanup_interval 秒（預設 60）清除一次
#   過期的項目
# - 清理任務會在第一次呼叫 get/set/set_if_absent/increment/
#   get_and_delete 時才延遲啟動
# - get() 會當場刪除過期的項目並回報未命中，不必等待
#   清理任務
# - 快取鍵不加前綴
```

### 網路後端共用的序列化（[`backends/codec.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/backends/codec.py)） {#serialization-shared-by-the-network-backends-backendscodecpy}

Redis 與 Memcached 共用同一套 JSON 編解碼器；若已安裝 `orjson` 就使用它，否則使用標準函式庫的 `json`：

```json
{
  "fingerprint": "W/\"abc123\"",
  "content": "<response bytes decoded as latin-1>",
  "media_type": "application/json",
  "status_code": 200,
  "headers": {"Vary": "Accept-Encoding"}
}
```

- `content` 使用 **latin-1 來回轉換**，而不是 base64：latin-1 與位元組一一對應，因此任何位元組序列都能放進 JSON 文字中，並原封不動地還原。
- 舊版本寫入、沒有 `status_code`／`headers` 欄位的項目仍可讀取，解碼後為 `200` 且沒有額外標頭。
- 任何解碼失敗（損壞的 JSON、缺少欄位、型別錯誤）都視為**快取未命中**，回傳 `None` 而不是拋出例外。
- `increment()` 會留下一個**單純的整數**（由 Redis／Memcached 的 INCR 系列指令寫入）；它會解碼成 fingerprint 為 `counter` 的 `CacheEntry`。

### MemcachedBackend {#memcachedbackend}

```
key:   "fastapi_cachex:GET|||example.com|||/api/users|||"
value: 上述的 JSON 文件

# 特性：
# - 若加上命名空間後的快取鍵包含空白、控制字元或非 ASCII
#   位元組，或超過 250 位元組，會改以其 SHA-256 十六進位摘要儲存
#   （`fastapi_cachex:<sha256>`）；否則 Memcached 會拒絕它，
#   請求會以 500 失敗
# - 超過 30 天的 TTL 會以絕對的 epoch 時間戳記送出；否則
#   Memcached 會把它解讀為 1970 年的某個時刻，使項目立即過期
# - 協定無法列舉快取鍵，因此 clear_pattern()/get_all_keys()/
#   get_cache_data() 都是 no-op，回傳 0/[]/{} 並發出 RuntimeWarning；
#   因此 CacheManager.clear()/clear_prefix() 在此後端上不會有任何作用
# - clear_path() 只會刪除與指定路徑完全相同的快取鍵，因此
#   無法清除 HTTP 路由的項目
# - clear() 會送出 flush_all，清空「整個」Memcached 伺服器（不只是
#   這個快取鍵前綴）並發出 RuntimeWarning
# - 同步的 pymemcache 用戶端在 worker 執行緒中執行，並使用連線
#   池與 default_noreply=False
```

### AsyncRedisCacheBackend {#asyncrediscachebackend}

```
key:   "fastapi_cachex:GET|||example.com|||/api/users|||"
value: 上述的 JSON 文件

# 特性：
# - 以 SET ... EX <ttl> 設定過期時間（ttl 為 None 時使用一般的 SET）
# - 模式操作以 SCAN（COUNT=100）分頁走訪快取鍵，而不是使用 KEYS，
#   因此永遠不會阻塞伺服器
# - clear() 只移除此後端快取鍵前綴下的快取鍵
# - get_and_delete() 使用 GETDEL（需要 Redis 6.2 以上）；刪除操作以
#   每批最多 100 個快取鍵的 DEL 送出
# - increment() 執行已註冊的 Lua 腳本，因此遞增與設定 TTL
#   是單一的原子操作
```

> [!NOTE]
> `add_routes()` 掛載的 `/cached-hits` 與 `/cached-records` 路由從 `get_cache_data()` 讀取過期時間。記憶體後端直接追蹤過期時間，Redis 回報每個快取鍵的 `PTTL`；在無法列舉快取鍵的 Memcached 上，這些端點完全不會回傳任何項目。

## 快取清除策略 {#cache-clearing-strategies}

### 自動清除 {#automatic-clearing}

```python
# MemoryBackend：每 cleanup_interval 秒（預設 60）清除一次
async def cleanup_task():
    while True:
        await asyncio.sleep(self.cleanup_interval)
        # 移除所有 CacheItem.expiry 已過的項目


# 此任務只會在第一次呼叫 get/set/set_if_absent/increment/
# get_and_delete 時延遲啟動（它需要執行中的事件迴圈），因此只寫入的
# 用法（例如 StateManager.create_state）也會啟動它。

# Redis/Memcached：TTL 機制
# 使用後端內建的 TTL（SET ... EX、exptime）
# 項目會自行過期；不需要清理任務
```

### 手動清除 {#manual-clearing}

`clear_path()`、`clear_pattern()`、`clear()` 與 `invalidate()` 的說明請見 [HTTP 快取](HTTP_CACHING.md#clearing-the-cache)。

## 效能 {#performance}

快取命中時，端點的 handler 完全不會執行：成本只有一次後端查詢。該選擇哪個後端，請見[後端](BACKENDS.md#choosing-a-backend)。

## 快取失效情境 {#cache-invalidation-scenarios}

| 情境 | 行為 |
|------|------|
| `no_store=True` | 既不讀取也不寫入快取；端點每次都會執行 |
| `no_cache=True` | 端點每次都會執行以重新計算 ETag；與用戶端的 `If-None-Match` 相符時仍回傳 304，ETag 改變時會更新快取 |
| `private=True` | **共用後端**既不讀取也不寫入；仍會送出 `Cache-Control: private`，並以新產生的內容比對 ETag |
| 沒有 `ttl` | 項目寫入時不設過期時間，但只用於 `If-None-Match` 重新驗證；沒有相符驗證器的請求每次都會執行 handler |
| 快取過期（TTL 已到） | 端點會再次執行；`MemoryBackend` 讀取到過期項目時會當場刪除 |
| 非 2xx 或 206 回應 | 原樣回傳、不寫入，既有的項目不受影響 |
| 串流／檔案回應 | 無法計算 ETag；原樣回傳且不寫入 |
| 手動呼叫 `invalidate()` | 刪除該路由的快取鍵 |
| 手動呼叫 `clear_path()`／`clear_pattern()`／`clear()` | 依範圍清除（在 Memcached 上，`clear_path()` 只刪除完全相符的快取鍵，`clear_pattern()` 是 no-op，`clear()` 會清空整個伺服器） |

## 實作細節 {#implementation-details}

### 快取項目結構 {#cache-entry-structure}

實際的型別是定義在 [`fastapi_cachex/types.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/types.py) 的 dataclass：

```python
@dataclass
class CacheEntry:
    fingerprint: str  # ETag，格式為 W/"<md5>"
    content: bytes  # 原始回應位元組
    media_type: str | None = None
    status_code: int = 200  # 原樣重播
    headers: dict[str, str] | None = None  # 重播時送回


@dataclass
class CacheItem:
    value: CacheEntry
    expiry: float | None = None  # epoch 秒數；僅 MemoryBackend 使用
```

`headers` 儲存 handler 自行設定的標頭，但排除每次回應都必須重新計算或不得重播的欄位：`Set-Cookie`、`Content-Length`、`Transfer-Encoding`、`Connection`、`Date`、`ETag`、`Cache-Control` 與 `Content-Type`（`Content-Type` 會由 `media_type` 還原；兩者都儲存會使該標頭送出兩次）。

計數器（`backend.increment()`）同樣以 `CacheEntry` 表示：fingerprint 一律為 `counter`，`content` 為十進位數值的位元組，因此刪除、清除與監控都以相同方式處理它們。

### 請求流程程式碼範例 {#request-flow-code-example}

裝飾器內部的執行順序請見上方「3. 快取查詢」中的判斷邏輯；在使用端，你只需要：

```python
@app.get("/expensive")
@cache(ttl=3600)
async def expensive_endpoint():
    # 此函式只會在快取未命中（或需要重新驗證）時執行
    return await perform_calculation()
```

handler 不必自行宣告 `Request`：`@cache` 會在函式簽名中注入一個名為 `__cachex_request` 的 keyword-only 參數（若 handler 有 `**kwargs`，則放在它之前）。若 handler **已經**宣告了 `Request`（包括字串註記、`Annotated[...]` 或 `Request` 子類別），就會沿用該參數，不會注入任何東西。

## 常見問題 {#faq}

**Q：為什麼快取命中不一定回傳 200？** A：視情況而定。若請求帶有 `If-None-Match` 標頭且其 ETag 相符，會回傳 304 以節省頻寬。沒有此標頭時，則回傳帶有內容的 200。

**Q：為什麼 POST／PUT 的回應不會被快取？** A：`@cache` 只適用於 GET。其他所有方法都直接執行 handler，不讀取或寫入快取，也不會加上 `Cache-Control` 標頭。

**Q：為什麼同一個端點有好幾個快取項目？** A：因為快取鍵包含查詢參數，而且查詢參數**不會排序**。`/users?page=1` 與 `/users?page=2` 是不同的項目，`?a=1&b=2` 與 `?b=2&a=1` 也是。

**Q：MemoryBackend 在多個行程下如何運作？** A：無法運作。每個行程都有自己的快取；正式環境請使用 Redis。

**Q：清除快取是同步還是非同步？** A：非同步：`await cache.clear_path(...)` 或 `await cache.clear_pattern(...)`。請注意，`clear_pattern()`（以及 `get_all_keys()` 與 `CacheManager.clear()`）在 Memcached 後端上是 no-op，因為 Memcached 協定無法列舉快取鍵。
