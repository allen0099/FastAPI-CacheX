# FastAPI-CacheX 快取流程 {#fastapi-cachex-cache-flow}

本文件詳細說明 FastAPI-CacheX 如何將快取邏輯套用到 HTTP 請求上。裝飾器位於 [`fastapi_cachex/cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/cache.py)，它呼叫的各部分則位於同一目錄的私有模組：[`_key_builders.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/_key_builders.py)（快取鍵）、[`_vary.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/_vary.py)（`vary=`）、[`_cache_control.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/_cache_control.py)（`Cache-Control`）、[`_stored_response.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/_stored_response.py)（儲存與重播回應、ETag 與 304）、[`_rendering.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/_rendering.py)（執行 handler 並加上依賴項的標頭），以及 [`_coalesce.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/_coalesce.py)（`coalesce=`）。

## 整體流程 {#overall-flow}

```
HTTP 請求抵達
    ↓
@cache 裝飾器攔截請求（只有 GET 與 HEAD 會經過快取，HEAD
讀取 GET 的項目；其他所有方法直接執行 handler，也不會帶上
Cache-Control 標頭）
    ↓
no-store？ ── 是 → 執行 handler，既不讀取也不寫入快取，
    │              回應帶上 Cache-Control: no-store
    ↓ 否
private、沒有正數的 ttl，或帶有 Authorization／Session 且未設定 public／cache_authorized？
    ── 是 → 執行 handler；比對 If-None-Match 決定回傳 304 或 200
    │        （共用後端既不讀取也不寫入，key builder 也不會執行；
    │         Authorization 或 Session 的情況下 Cache-Control 以 private 取代 public）
    ↓ 否
（設定 cache_authorized 且帶有 Authorization 或 Session：下方照常使用後端，
但每個回應仍以 private 取代 public）
    ↓
建立快取鍵：key_builder（預設為 http:v2|method|host|path|query_params），
再為每個 vary 標頭附加一個 name=value 段
    ↓
讀取後端項目（fail_open 時，後端錯誤視為未命中）
    ↓
請求帶有 If-None-Match？
    ├─ 且為 no-cache → 先執行 handler 計算目前的 ETag；相符 → 304
    ├─ 其他情況      → 與快取項目的 ETag 比對；相符 → 帶 Age 的 304
    └─ 不相符／沒有此標頭 → 繼續
    ↓
快取項目存在，且未啟用 no-cache？
    ├─ 是 → 以快取內容回應（包含儲存的狀態碼
    │        與標頭，並加上 Age；handler **不會**執行）
    └─ 否 → 執行 handler
              ├─ 非 2xx（或 206）→ 原樣回傳且**不寫入**
              │                    （不會覆寫既有的正常項目）
              ├─ 串流／檔案回應 → 無法計算 ETag；原樣回傳，不寫入
              ├─ handler 送出 Cache-Control private／no-store，或 Set-Cookie
              │     → 設定 ETag 後回傳，**不寫入**（既有項目保持不變；
              │       private／no-store 標頭保留原樣）
              └─ 一般回應 → 設定 ETag；只有與既有項目的 ETag
                            不同時才寫入後端
                            （fail_open 時，寫入失敗會記錄警告，
                            回應照常送出但不儲存）
              接著，若 If-None-Match 與新的 ETag 相符（項目已過期、
              已清除或被淘汰、從未儲存，或存在另一個 worker 中）
              → 以 304 取代 200，並帶上 Set-Cookie 與 handler 的背景任務
    ↓
在回應中附加 Cache-Control（非 2xx 回應回傳時不帶此標頭，
handler 自己送出的 private／no-store Cache-Control 永遠不會被取代，
設定 Set-Cookie 的回應則以 private 取代 public）；設定 vary 時，
會把這些名稱加入每個 GET 或 HEAD 回應的 Vary
```

## 詳細步驟 {#detailed-steps}

### 1. 請求攔截與快取鍵產生 {#1-request-interception-and-key-generation}

請求抵達時，`@cache` 裝飾器會執行以下步驟：

```python
from fastapi_cachex import CacheKey
from fastapi_cachex.types import escape_key_component

# 快取鍵格式（fastapi_cachex/cache_key.py 中的 CacheKey；
# build_cache_key(request) 即 CacheKey.from_request(request).to_str()）
cache_key = "|".join(
    [
        CacheKey.FORMAT_TAG,  # "http:v2"
        escape_key_component(request.method),
        escape_key_component(host),  # Host 標頭，已正規化（見下文）
        escape_key_component(request.url.path),
        query,
    ]
)

# 例如：
# http:v2|GET|example.com|/api/users|limit=10&page=1  （對應 ?page=1&limit=10）
# http:v2|GET|api.example.com|/api/users/123|
```

每個快取鍵都以格式標籤 `http:v2` 開頭。其他格式的鍵（0.3.x 寫入的是沒有標籤的 `GET|||host|||path|||query`）不會與這些鍵衝突，在 Redis 與記憶體後端上，`clear_pattern("http:v2|*")` 會移除這個格式的所有鍵。`CacheKey.parse()` 只會讀取帶有這個標籤的鍵。

分隔符號使用 `|` 而不是冒號，是因為 host 本身可能包含連接埠（`127.0.0.1:8000`）；若使用冒號，快取鍵就無法可靠地拆分，而 `clear_path()` 需要從快取鍵中取回路徑。

方法、host 與路徑會先經過百分比編碼：`|` 變成 `%7C`，`%` 變成 `%25`（`fastapi_cachex/types.py` 中的 `escape_key_component`）。host 與路徑來自用戶端，其中若出現未編碼的 `|`，各段就會錯位，使某個請求的快取鍵可能與另一個請求相同。查詢字串本來就經過 URL 編碼，因此不會含有 `|`。監控路由顯示時會再解碼各段。

自訂的 `key_builder` 可以用 `build_cache_key(request, *components)` 在查詢字串之後加入其他段；這些段以同樣方式編碼，`clear_path()` 也仍會比對路徑（見 [HTTP 快取](HTTP_CACHING.md#adding-components-to-the-key)中的「在鍵中加入其他段」）。`@cache(vary=[...])` 會在 key builder 回傳的鍵之後，為每個列出的請求標頭附加一個 `name=value` 段，並把這些名稱加入回應的 `Vary` 標頭（見 [HTTP 快取](HTTP_CACHING.md#varying-on-request-headers)中的「依請求標頭區分」）。對於憑證標頭 `Authorization`、`Proxy-Authorization`、`Cookie` 與 `X-Session-Token`，非空的值會寫成 `sha256:<十六進位摘要>`，因此鍵中不會出現任何權杖。

查詢參數會依名稱**排序**（穩定排序：同名參數的多個值保留送出的順序），因此 `?page=1&limit=10` 與 `?limit=10&page=1` 共用同一個快取項目。`@cache(sort_query=False)` 則保留請求送出的順序（見 [HTTP 快取](HTTP_CACHING.md#cache-keys)中的「快取鍵」）。超過 200 位元組的查詢接著會改寫為 `sha256:<十六進位摘要>`，讓鍵中查詢的部分維持有限長度。

這個快取鍵格式讓每個維度各自獨立快取：

- **方法隔離**：GET 與 POST 不共用快取（而且只有 GET 與 HEAD 會進入快取流程；HEAD 使用 GET 的快取鍵，且永遠不會儲存）
- **Host 隔離**：`example.com` 與 `api.example.com` 分開快取；`Example.com` 與（在 http 上的）`example.com:80` 都是 `example.com`
- **路徑隔離**：每個端點有各自的項目
- **查詢參數隔離**：同一端點上不同的查詢參數分開快取

### 2. Cache-Control 指令 {#2-cache-control-directives}

裝飾器參數同時控制伺服器端的行為，以及送給用戶端的 `Cache-Control` 標頭：

```python
# 改變伺服器端快取的使用方式
@cache(no_cache=True)     # 每次都重新執行 handler（重新驗證）；ttl 為正數時項目仍會寫入
@cache(no_store=True)     # 永不讀取或寫入快取

# 一般快取行為
@cache(ttl=3600)          # 快取 1 小時（也作為 max-age 的值）
@cache(ttl=3600, public=True)     # 允許共用快取，帶有 Authorization／Session 的請求也一樣
@cache(private=True)      # 僅限私有；永遠不接觸共用後端
@cache(ttl=60, key_builder=per_user_key, cache_authorized=True)  # 帶有 Authorization／Session 的請求也使用後端，以 private 回應
@cache(ttl=3600, immutable=True)  # 內容永不改變

# 只影響標頭的指令（不會改變伺服器端行為）
@cache(ttl=60, must_revalidate=True)                        # must-revalidate
@cache(ttl=60, stale="revalidate", stale_ttl=30)            # stale-while-revalidate=30
@cache(ttl=60, stale="error", stale_ttl=300)                # stale-if-error=300
```

參數會在套用裝飾器時驗證；若同時設定 `public` 與 `private`、只提供 `stale`／`stale_ttl` 其中之一、`ttl` 或 `stale_ttl` 不是 `int` 或整數秒的 `timedelta`、為負數或大於 `MAX_TTL`、`vary` 不是由標頭欄位名稱組成的 list、`sort_query` 不是 `bool` 或與自訂的 `key_builder` 一起傳入，`key_builder` 是 `async` 可呼叫物件，或 `coalesce` 不是 `bool`、未搭配正的 `ttl` 或與 `private`、`no_cache` 一起設定，會拋出 `CacheXError`。

標頭值在每個被裝飾的路由上只建立一次：

| 參數 | 送出的 `Cache-Control` |
|------|------------------------|
| `no_store=True` | `no-store`（覆蓋其他所有設定） |
| `no_cache=True` | `no-cache`，若有要求則加上 `must-revalidate`；省略 `public`／`private`／`max-age`／`stale-*`／`immutable` |
| 無（不帶參數的 `@cache()`） | 不送出自己的標頭；保留 handler 的標頭（若有）（遇到 Cookie 或憑證時送出 `private`，與其他路由相同） |
| 其他情況 | 依序為：`public` 或 `private`、`max-age=<ttl>`、`must-revalidate`、`stale-while-revalidate=<n>` 或 `stale-if-error=<n>`、`immutable` |

> [!NOTE]
> 沒有設定 `ttl`（或設定 `ttl=0`，此時會送出 `max-age=0`）時，既不讀取也不寫入後端：每個請求都會執行 handler，相符的 `If-None-Match` 只有在與新產生的回應比對之後才會以 `304` 回應。請設定正數的 `ttl`，讓伺服器儲存並重播回應。

> [!WARNING]
> **預設的快取鍵不包含使用者身分**，而且後端由所有 worker 與所有使用者共用。直接在需要驗證的端點上加上 `@cache(ttl=...)`，會把使用者 A 的回應提供給下一個請求相同路徑的使用者 B。
>
> 對於回應內容取決於呼叫者的端點，請擇一處理：
>
> 1. `private=True`：永遠不讀取或寫入共用後端。它仍會送出 `Cache-Control: private`，讓使用者自己的瀏覽器可以快取回應，而且 `If-None-Match` 仍會與新產生的內容比對。
> 2. 包含身分的自訂 `key_builder`，並搭配 `cache_authorized=True`：當你確實需要以使用者為單位的伺服器端快取時使用。未設定 `cache_authorized` 時，帶有 `Authorization` 標頭或 Session 的請求會繞過後端（見下方說明）。設定之後，回應仍以 `private` 送出，因為下游的共用快取看不到鍵中的身分。
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
    headers=(
        ("link", "</a.css>; rel=preload"),
        ("link", "</b.js>; rel=preload"),
    ),  # 重播時依序送回的標頭行
    stored_at=1702650540.5,  # @cache 儲存它時的 epoch 秒數；用來計算 Age
)
```

TTL 不儲存在 `CacheEntry` 中：過期由後端負責（`MemoryBackend` 將它存在 `CacheItem.expiry`，Redis 使用 `SET ... EX`，Memcached 使用 exptime）。`stored_at` 是系統時鐘時間（`time.time()`），因為送出項目的行程不一定是儲存它的行程；0.3.9 之前的版本寫入的項目為 `None`。

若尚未以 `BackendProxy.set()` 設定後端，裝飾器會在第一個請求時建立 `MemoryBackend`、註冊它，並記錄一則警告，說明這個快取是每個行程各自一份。

**判斷邏輯**（`cache.py` 的包裝函式，依序執行）：

```python
if request.method not in ("GET", "HEAD"):
    return await handler()  # 不快取，不帶 Cache-Control

if no_store:
    return await render()  # 不讀取，不寫入

bypass = private or not ttl
# Authorization 標頭、中介軟體載入的 Session，或不是空的 request.session
credential = None if private or public else request_credential(request)
header = private_header if credential else decorator_header  # 用於下方每個回應
if bypass or (credential and not cache_authorized):
    response, etag = await render()  # 既不讀取也不寫入後端
    return not_modified(...) if etag_matches(client_etag, etag) else response

# HEAD：key_builder 拿到的請求方法是 GET
cache_key = key_builder(request) + vary_components(request)  # 只在這裡建立
entry = await backend.get(cache_key)  # 過期的項目已在此略過
if coalesce and entry is None and (leader := running_miss(cache_key)):
    await leader  # 只有 GET 會帶頭；HEAD 只會等待
    entry = await backend.get(cache_key)  # None：在下方自行產生，不再等待

if client_etag and no_cache:
    fresh = await render()  # no-cache：一律先重新產生
    if etag_matches(client_etag, fresh.etag):
        return not_modified(...)  # 304
elif client_etag and entry and etag_matches(client_etag, entry.fingerprint):
    return not_modified(..., age_headers(entry, ttl))  # 304，handler 不執行

if entry and not no_cache:
    hit = Response(  # 200，handler 不執行
        content=entry.content,
        status_code=entry.status_code,
        media_type=entry.media_type,
    )
    for name, value in entry.headers:  # 依序加上儲存的每一行
        hit.headers.append(name, value)
    hit.headers["ETag"] = entry.fingerprint  # 接著設定 ETag、Cache-Control 與
    ...  # Age：now - stored_at，限制在 0..ttl
    return hit

response, body, etag = await render()  # 未命中（若 no-cache 已產生過則直接沿用）
if not is_cacheable_status(response.status_code):
    return response  # 非 2xx：原樣回傳，不寫入
if etag is None:
    return response  # 串流／檔案：沒有 ETag，不寫入
shareable = not (
    marked_private_or_no_store(response)
    or "set-cookie" in response.headers
    or request.method == "HEAD"
)  # 屬於單一呼叫者的回應與 HEAD 的回應不寫入
if shareable and (not entry or entry.fingerprint != etag):
    await backend.set(cache_key, CacheEntry(..., stored_at=time.time()), ttl=ttl)
if etag_matches(client_etag, etag):
    return not_modified(...)  # 304：用戶端的副本仍是最新的；
    # 它保留回應的 Set-Cookie 與背景任務
return response
```

> [!NOTE]
> **由後端送出的回應帶有 `Age`。** 快取命中，以及依已儲存 ETag 回應的 304，會帶有 `Age: <自 stored_at 起的秒數>`，並限制在 `0`–`ttl` 之間，以防主機之間的時鐘偏差；`Cache-Control` 保持 `max-age=<ttl>`，由下游快取從中扣掉 `Age`（RFC 9111 §4.2.3）。handler 剛產生的回應（包括所有 `no_cache` 回應與所有繞過後端的回應）不帶 `Age`，沒有 `stored_at` 的項目也不帶。

> [!NOTE]
> 「非 2xx 不寫入」是刻意的設計：暫時性的錯誤不應抹除最後一次正常的快取回應，也不應在之後被當成 200 重播。`206 Partial Content` 同樣不會快取，因為它的內容只對產生它的那個 `Range` 請求有意義。非 2xx 回應也永遠不會以 `304` 回應，且回傳時不帶裝飾器的 `Cache-Control` 標頭（只有 `no_store=True` 會在每個回應加上 `no-store`）。

> [!NOTE]
> **屬於單一呼叫者的回應永遠不會被儲存。** 依照 RFC 9111 §3.5，帶有 `Authorization` 標頭的請求會繞過後端（不讀取也不寫入），帶有 Session 的請求（Session 中介軟體從任何權杖來源載入了 Session，或 `request.session` 不是空的）也一樣，除非路由設定了 `public=True`，或以 `cache_authorized=True` 明確選擇啟用（用於包含已驗證身分的 `key_builder`）。產生回應時，若回應自己的 `Cache-Control` 含有 `private` 或 `no-store`（完整指令，不分大小寫），或回應設定了 cookie，則照常回傳但不寫入。handler 送出的 `private`／`no-store` 標頭會原樣送出，不會被裝飾器的標頭取代。設定 cookie 的回應，以及任何 `Authorization` 或 Session 請求的回應（無論繞過後端，或設定 `cache_authorized` 而由後端回應；200 或 304），會以 `private` 取代 `public` 送出並保留裝飾器的其他指令（`no_cache` 路由則為 `private, no-cache`），讓下游的共用快取也不會儲存它們。`must_revalidate=True` 不會解除 `Authorization` 的繞過；雖然 RFC 9111 允許在 `must-revalidate` 下重複使用，本函式庫仍要求明確選擇啟用。該鍵下已儲存的項目保持不變，而在 handler 執行前就命中該項目的請求仍照常由它回應。每次略過都會以 `DEBUG` 等級記錄。0.3.9 之前這類回應會被儲存並重播給每位呼叫者（#296）；帶有 Session 的請求在 0.3.9 之前也不會繞過（#319）。

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

304 回應會帶有與 200 相同的 `Cache-Control` 與 `ETag`，以及影響快取行為的標頭 `Vary`、`Content-Location` 與 `Expires`；否則中介快取在重新驗證後會遺失這些欄位（RFC 9110 §15.4.5）。針對 handler 新產生之回應的 304 另外會帶上 handler 的 `Set-Cookie`（這類回應一律以 `private` 送出）。

## 後端儲存格式 {#backend-storage-formats}

### MemoryBackend {#memorybackend}

```python
# dict[str, CacheItem]；CacheItem 包裝 CacheEntry 並記錄其過期時間
{
    "http:v2|GET|example.com|/api/users|": CacheItem(
        value=CacheEntry(
            fingerprint='W/"abc123"',
            content=b"...",
            media_type="application/json",
            status_code=200,
            headers=(),
            stored_at=1702650540.5,
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
  "headers": [["vary", "Accept-Encoding"]],
  "stored_at": 1702650540.5
}
```

- `content` 使用 **latin-1 來回轉換**，而不是 base64：latin-1 與位元組一一對應，因此任何位元組序列都能放進 JSON 文字中，並原封不動地還原。
- 舊版本寫入、沒有 `status_code`／`headers` 欄位的項目仍可讀取，解碼後為 `200` 且沒有額外標頭。沒有 `stored_at` 的項目（0.3.9 之前）解碼後為 `stored_at=None`，送出時不帶 `Age` 標頭。
- `headers` 是 `[name, value]` 行組成的清單，因此重複送出的標頭會保留每一行。0.3.x 寫入的物件（每個名稱一個值）仍可解碼。其他任何形式都會讓整個項目視為未命中。
- 任何解碼失敗（損壞的 JSON、缺少欄位、型別錯誤）都視為**快取未命中**，回傳 `None` 而不是拋出例外。
- `increment()` 會留下一個**單純的整數**（由 Redis／Memcached 的 INCR 系列指令寫入）；它會解碼成 fingerprint 為 `counter` 的 `CacheEntry`。

### MemcachedBackend {#memcachedbackend}

```
key:   "fastapi_cachex:http:v2|GET|example.com|/api/users|"
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
#   無法清除 HTTP 路由的項目，而且每次呼叫都會發出 RuntimeWarning；
#   要刪除快取路由的項目請使用 invalidate(request)
# - clear() 會送出 flush_all，清空「整個」Memcached 伺服器（不只是
#   這個快取鍵前綴）並發出 RuntimeWarning
# - 同步的 pymemcache 用戶端在 worker 執行緒中執行，並使用連線
#   池與 default_noreply=False
```

### AsyncRedisCacheBackend {#asyncrediscachebackend}

```
key:   "fastapi_cachex:http:v2|GET|example.com|/api/users|"
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
# 用法（例如 CacheManager.add()）也會啟動它。

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
| 帶有 `Authorization` 或 Session 的請求 | 與 `private=True` 一樣，既不讀取也不寫入後端，`Cache-Control` 以 `private` 取代 `public`，除非路由設定了 `public=True` 或 `cache_authorized=True`（`must_revalidate=True` 不算）；設定 `cache_authorized=True` 時會使用後端，但 `Cache-Control` 仍帶有 `private` |
| handler 送出 `Cache-Control: private`／`no-store` | 回傳時保留 handler 的標頭，不寫入，既有項目也保持不變 |
| 回應設定了 cookie | 回傳時 `Cache-Control` 以 `private` 取代 `public`，不寫入，既有項目也保持不變 |
| 沒有 `ttl`（或 `ttl=0`） | 與 `private=True` 一樣，既不讀取也不寫入後端；端點每次都會執行，並以新產生的內容比對 ETag |
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
    headers: tuple[tuple[str, str], ...] = ()  # 重播時送回的 (name, value) 標頭行
    stored_at: float | None = None  # @cache 儲存它時的 epoch 秒數；用來計算 Age


@dataclass
class CacheItem:
    value: CacheEntry
    expiry: float | None = None  # epoch 秒數；僅 MemoryBackend 使用
```

`headers` 儲存 handler 自行設定的標頭，但排除每次回應都必須重新計算或不得重播的欄位：`Set-Cookie`、`Content-Length`、`Transfer-Encoding`、`Connection`、`Date`、`ETag`、`Cache-Control`、`Content-Type` 與 `Age`（`Content-Type` 會由 `media_type` 還原；兩者都儲存會使該標頭送出兩次）。

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

**Q：為什麼 POST／PUT 的回應不會被快取？** A：`@cache` 只適用於 GET（以及以 GET 的項目回應的 HEAD）。其他所有方法都直接執行 handler，不讀取或寫入快取，也不會加上 `Cache-Control` 標頭。

**Q：為什麼同一個端點有好幾個快取項目？** A：因為快取鍵包含查詢參數。`/users?page=1` 與 `/users?page=2` 是不同的項目；若路由設定了 `sort_query=False`，`?a=1&b=2` 與 `?b=2&a=1` 也是。

**Q：MemoryBackend 在多個行程下如何運作？** A：無法運作。每個行程都有自己的快取；正式環境請使用 Redis。

**Q：清除快取是同步還是非同步？** A：非同步：`await cache.clear_path(...)` 或 `await cache.clear_pattern(...)`。請注意，`clear_pattern()`（以及 `get_all_keys()` 與 `CacheManager.clear()`）在 Memcached 後端上是 no-op，因為 Memcached 協定無法列舉快取鍵。
