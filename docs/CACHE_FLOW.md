# FastAPI-CacheX 快取流程說明

本文件詳細說明 FastAPI-CacheX 如何處理 HTTP 請求的快取邏輯。

## 整體流程圖

```
HTTP 請求到達
    ↓
@cache 裝飾器攔截（只有 GET 會走快取，其餘方法直接執行處理器）
    ↓
生成快取金鑰: method|||host|||path|||query_params
    ↓
no-store？ ── 是 → 執行處理器，不讀也不寫快取
    ↓ 否
private？ ── 是 → 執行處理器；比對 If-None-Match 決定 304 或 200
    │              （不讀也不寫共享後端）
    ↓ 否
讀取後端條目
    ↓
請求帶 If-None-Match？
    ├─ 且 no-cache → 先執行處理器算出最新 ETag，相符 → 304
    ├─ 一般情況   → 與快取條目的 ETag 比對，相符 → 304
    └─ 不符 / 無此標頭 → 往下
    ↓
有快取條目、有 ttl、且非 no-cache
    ├─ 是 → 直接以快取內容回應（連同存下來的狀態碼與標頭；處理器 **不執行**）
    └─ 否 → 執行處理器
              ├─ 非 2xx（或 206）→ 原樣回傳，且 **不寫入**（不覆蓋既有好條目）
              ├─ 串流／檔案回應 → 無法計算 ETag，原樣回傳，不寫入
              └─ 一般回應 → 設定 ETag；與既有條目 ETag 不同才寫入後端
    ↓
回應附上 Cache-Control
```

## 詳細步驟

### 1. 請求攔截與金鑰生成

當請求到達時，`@cache` 裝飾器會：

```python
from fastapi_cachex.types import CACHE_KEY_SEPARATOR  # "|||"

# 快取金鑰格式（fastapi_cachex/cache.py 的 default_key_builder）
cache_key = CACHE_KEY_SEPARATOR.join(
    [request.method, request.headers.get("host", "unknown"), request.url.path, query]
)

# 例如：
# GET|||example.com|||/api/users|||page=1&limit=10
# GET|||api.example.com|||/api/users/123|||
```

分隔符用 `|||` 而不是冒號，是因為 host 本身可能含連接埠（`127.0.0.1:8000`），
用冒號會讓金鑰無法被正確拆解 —— `MemoryBackend.clear_path()` 需要從金鑰反解出路徑。

查詢參數是照請求原本的順序串接（`str(request.query_params)`），**不會排序**，
所以 `?page=1&limit=10` 與 `?limit=10&page=1` 是兩份獨立的快取條目。需要把兩者
視為同一份時，請自訂 `key_builder` 做正規化。

金鑰格式確保不同維度的資料獨立快取：
- **方法隔離**：GET 和 POST 不共享快取（且目前只有 GET 會進入快取流程）
- **主機隔離**：`example.com` 和 `api.example.com` 分別快取
- **路徑隔離**：不同端點各自快取
- **查詢參數隔離**：同一端點不同查詢參數分別快取

### 2. Cache-Control 指令檢查

裝飾器檢查各種快取指令：

```python
# 完全跳過快取
@cache(no_cache=True)     # 強制重新驗證
@cache(no_store=True)     # 不儲存任何內容

# 正常快取行為
@cache(ttl=3600)          # 快取 1 小時（同時作為 max-age 的值）
@cache(public=True)       # 允許共享快取
@cache(private=True)      # 僅私有快取，不進共享後端
@cache(immutable=True)    # 內容永不變更
```

> [!WARNING]
> **預設快取金鑰不含使用者身分**，而後端是所有 worker、所有使用者共用的。
> 直接對需要驗證的端點使用 `@cache(ttl=...)`，會把 A 使用者的回應供應給下一
> 個請求同一路徑的 B 使用者。
>
> 需要依請求者而異的端點，擇一處理：
>
> 1. `private=True` — 完全不讀寫共享後端。仍會輸出 `Cache-Control: private`
>    讓使用者自己的瀏覽器快取，`If-None-Match` 也仍以即時算出的內容比對。
> 2. 自訂含身分的 `key_builder` — 當你確實想要「每位使用者一份」的伺服器端快取。
>
> 身分請取自可信來源（已驗簽的 token claim、依賴注入的使用者物件），
> 不要直接採信未經檢查的客戶端標頭。

### 3. 快取查詢

根據快取金鑰查詢後端。後端回傳的是 `CacheEntry`（過期條目由後端自己判斷並跳過）：

```python
from fastapi_cachex.types import CacheEntry

entry = CacheEntry(
    fingerprint='W/"9f86d081..."',  # ETag，內容的弱驗證器
    content=b'{"data": "response"}',  # 原始回應位元組
    media_type="application/json",
    status_code=200,  # 重播時沿用原本的狀態碼
    headers={"Vary": "Accept-Encoding"},  # 重播時一併帶回的標頭
)
```

TTL 不存在 `CacheEntry` 裡：到期時間是後端的責任（`MemoryBackend` 記在
`CacheItem.expiry`，Redis 用 `SETEX`，Memcached 用 exptime）。

**決策邏輯**（`cache.py` 的 wrapper，依序）：

```python
if no_store:
    return await render()                    # 不讀、不寫

if private:
    response, etag = await render()          # 不讀、不寫共享後端
    return not_modified(...) if etag_matches(client_etag, etag) else response

entry = await backend.get(cache_key)         # 過期條目在這裡就已被跳過

if client_etag and no_cache:
    fresh = await render()                   # no-cache：一律先重算
    if etag_matches(client_etag, fresh.etag):
        return not_modified(...)             # 304
elif client_etag and entry and etag_matches(client_etag, entry.fingerprint):
    return not_modified(...)                 # 304，處理器不執行

if entry and not no_cache and ttl is not None:
    return Response(                         # 200，處理器不執行
        content=entry.content,
        status_code=entry.status_code,
        media_type=entry.media_type,
        headers={**(entry.headers or {}), "ETag": entry.fingerprint, ...},
    )

response, body, etag = await render()        # 未命中
if not is_cacheable_status(response.status_code):
    return response                          # 非 2xx：原樣回傳且不寫入
if etag is None:
    return response                          # 串流／檔案：無法計算 ETag，不寫入
if not entry or entry.fingerprint != etag:
    await backend.set(cache_key, CacheEntry(...), ttl=ttl)
return response
```

> [!NOTE]
> 「非 2xx 不寫入」是刻意的：暫時性錯誤不該把上一份好的快取洗掉，也不該之後被
> 以 200 重播。`206 Partial Content` 同樣不快取。

### 4. ETag 生成與驗證

ETag 由回應內容算出，用來驗證內容是否變更：

```python
# 生成：MD5，並標成弱驗證器
def _etag_for(body: bytes) -> str:
    return f'W/"{hashlib.md5(body).hexdigest()}"'
```

`If-None-Match` 依 RFC 9110 §8.8.3.2 以**弱比對**判斷，因此：

```
If-None-Match: W/"abc"            → 與 "abc" 視為相符（兩側都忽略 W/ 前綴）
If-None-Match: "abc", W/"def"     → 多值，逐一比對，任一相符即 304
If-None-Match: *                  → 資源存在即相符 → 304
```

回 304 時，會一併帶回 200 會帶的 `Cache-Control`/`ETag` 以及會影響快取的標頭
（`Vary`、`Content-Location`、`Expires` 等），否則中介快取在重新驗證後會把這些
欄位弄丟（RFC 9110 §15.4.5）。

## 後端存儲格式

### MemoryBackend

```python
# dict[str, CacheItem]，CacheItem 包住 CacheEntry 並額外記到期時間
{
    "GET|||example.com|||/api/users|||": CacheItem(
        value=CacheEntry(
            fingerprint='W/"abc123"',
            content=b"...",
            media_type="application/json",
            status_code=200,
            headers=None,
        ),
        expiry=1702650600.5,  # epoch 秒；None 表示永不過期
    ),
}

# 特點：
# - 儲存於行程內記憶體，不跨行程共享
# - 背景清理任務每 cleanup_interval 秒（預設 60）掃掉過期項目
# - 清理任務在第一次 get/set/increment/get_and_delete 時延遲啟動
# - get() 讀到過期項目時會就地刪除並回報 miss，不等清理任務
```

### 網路後端共用的序列化（`backends/codec.py`）

Redis 與 Memcached 共用同一份 JSON 編解碼；裝了 `orjson` 就用它，否則用標準庫 `json`：

```json
{
  "fingerprint": "W/\"abc123\"",
  "content": "<回應位元組以 latin-1 解碼後的字串>",
  "media_type": "application/json",
  "status_code": 200,
  "headers": {"Vary": "Accept-Encoding"}
}
```

- `content` 走的是 **latin-1 round-trip**，不是 base64：latin-1 與位元組一一對應，
  所以任何位元組都能安全地放進 JSON 文字再原樣取回。
- 舊版寫入、沒有 `status_code`/`headers` 欄位的條目仍可讀，會解成 `200` 且無額外標頭。
- 解碼失敗（JSON 壞掉、欄位缺漏、型別不對）一律當成 **cache miss** 回 `None`，不丟例外。
- `increment()` 留下的是**裸整數**（Redis/Memcached 的 INCR 家族所寫），解碼時會轉成
  fingerprint 為 `counter` 的 `CacheEntry`。

### MemcachedBackend

```
key:   "fastapi_cachex:GET|||example.com|||/api/users|||"
value: 上述 JSON 內容

# 特點：
# - 金鑰含空白/控制字元或超過 250 bytes 時，整段金鑰改存 SHA-256 十六進位摘要
#   （`fastapi_cachex:<sha256>`），否則 Memcached 會直接拒絕並讓請求變成 500
# - TTL 超過 30 天時改送絕對 epoch 時間戳，否則會被解讀成 1970 年的時刻而立即過期
# - 協定沒有金鑰列舉能力，所以 clear_pattern()/get_all_keys() 是 no-op，
#   回 0/[] 並發出 RuntimeWarning；CacheManager.clear()/clear_prefix() 因此在此後端無效
# - 同步 pymemcache client 跑在 worker thread，開啟連線池與 default_noreply=False
```

### AsyncRedisCacheBackend

```
key:   "fastapi_cachex:GET|||example.com|||/api/users|||"
value: 上述 JSON 內容

# 特點：
# - 以 SETEX 設定到期（ttl 為 None 時用 SET）
# - 模式操作一律用 SCAN（COUNT=100）分頁走訪，不用 KEYS，不阻塞伺服器
# - get_and_delete() 用 GETDEL（需要 Redis 6.2+），刪除以 DEL 分批送出
# - increment() 走註冊過的 Lua script，計數與設定 TTL 是同一個原子操作
```

> [!NOTE]
> **監控端點的 TTL 欄位在 Redis 後端不可用。**
> `AsyncRedisCacheBackend.get_cache_data()` 對每個金鑰回 `(entry, None)`，沒有去查
> 每個金鑰的實際 TTL，所以 `add_routes()` 掛出來的 `/cached-records` 會把 Redis 條目
> 一律顯示為「永不過期」。實際過期仍由 Redis 自己執行，只是監控看不到剩餘秒數。

## 快取清除策略

### 自動清除

```python
# MemoryBackend: 每 cleanup_interval 秒（預設 60）掃一次
async def cleanup_task():
    while True:
        await asyncio.sleep(self.cleanup_interval)
        # 移除所有 CacheItem.expiry 已過的項目


# 這個任務在第一次 get/set/increment/get_and_delete 時才延遲啟動（需要有 event loop），
# 所以「只寫不讀」的用法（例如 StateManager.create_state）也會把它帶起來。

# Redis/Memcached: TTL 機制
# 使用後端的內置 TTL (SETEX, exptime)
# 項目自動過期，無需清理任務
```

### 手動清除

```python
# 清除特定路徑
await cache.clear_path("/api/users")  # 移除所有 host/method/params 組合

# 清除模式：比對的是完整金鑰 method|||host|||path|||query
await cache.clear_pattern("GET|||*|||/api/users/*")  # 移除 /api/users/... 的 GET 項目
await cache.clear_pattern("cache:user:*")  # 自己組的金鑰（如 CacheManager）直接比對

# 清除全部
await cache.clear()  # 移除所有快取項目
```

單獨讓某條已快取路由失效（例如寫入後要打掉對應的 GET 快取），用 top-level 的
`invalidate()`，它會用同一組 key_builder 重建金鑰再刪除：

```python
from fastapi_cachex import invalidate

removed: bool = await invalidate(request)  # 用預設 key_builder
removed = await invalidate(request, key_builder=my_key_builder)  # 路由有自訂時要一致
```

回傳值代表「原本是否存在該條目」。未設定後端時回 `False` 而不拋例外。

## 效能最佳化

### 快取命中路徑

```
請求 → 快取查詢 (< 5ms)
        ↓
        返回快取 (< 1ms)

總耗時: ~5-10ms (無需執行端點處理器)
相比直接執行: 節省 100-1000ms+ (取決於端點複雜度)
```

### 後端選擇建議

| 場景 | 推薦後端 | 原因 |
|------|--------|------|
| 開發測試 | MemoryBackend | 快速、無依賴 |
| 分散式系統 | Redis | 非同步、高效、支援模式清除 |
| 簡單快取 | Memcached | 穩定、成熟 |
| 多行程部署 | Redis | 共享快取、一致性 |

## 快取失效場景

| 場景 | 行為 |
|------|------|
| `no_store=True` | 不讀也不寫快取，每次都執行端點 |
| `no_cache=True` | 每次都執行端點重算 ETag；與客戶端 `If-None-Match` 相符時仍回 304，並在 ETag 變動時更新快取 |
| `private=True` | 不讀也不寫**共享後端**；仍輸出 `Cache-Control: private` 並以即時內容做 ETag 比對 |
| 快取過期（TTL 到期） | 重新執行端點；`MemoryBackend` 讀到過期條目會就地刪除 |
| 回應為非 2xx 或 206 | 原樣回傳，不寫入，也不覆蓋既有條目 |
| 回應為串流／檔案 | 無法計算 ETag，原樣回傳且不寫入 |
| 手動呼叫 `invalidate()` | 該路由對應金鑰被刪除 |
| 手動呼叫 `clear_path()` / `clear_pattern()` / `clear()` | 依範圍清除（Memcached 後端的後兩者為 no-op） |

## 實現細節

### 快取項目結構

實際型別是 dataclass，定義在 `fastapi_cachex/types.py`：

```python
@dataclass
class CacheEntry:
    fingerprint: str  # ETag，格式為 W/"<md5>"
    content: bytes  # 原始回應位元組
    media_type: str | None = None
    status_code: int = 200  # 重播時沿用
    headers: dict[str, str] | None = None  # 重播時一併帶回


@dataclass
class CacheItem:
    value: CacheEntry
    expiry: float | None = None  # epoch 秒；僅 MemoryBackend 使用
```

`headers` 存的是處理器自己設定的標頭，但會排除每次回應都要重算或不該重播的欄位：
`Set-Cookie`、`Content-Length`、`Transfer-Encoding`、`Connection`、`Date`、`ETag`、
`Cache-Control`、`Content-Type`（`Content-Type` 由 `media_type` 還原，存兩份會重複輸出）。

計數器（`backend.increment()`）也以 `CacheEntry` 呈現：fingerprint 固定為 `counter`，
`content` 是十進位數字的位元組，因此刪除、清除與監控都能一視同仁地處理。

### 請求流程程式碼範例

裝飾器內部的實際順序見上面〈3. 快取查詢〉的決策邏輯；使用端只需要：

```python
@cache(ttl=3600)
async def expensive_endpoint():
    # 此函數只在快取未命中（或需要重新驗證）時執行
    return await perform_calculation()
```

處理器不需要自己宣告 `Request`；`@cache` 會在簽名中注入一個名為 `__cachex_request`
的 keyword-only 參數。若處理器**已經**宣告了 `Request`（含字串註解、`Annotated[...]`
或 `Request` 子類），就直接沿用該參數，不會重複注入。

## 常見問題

**Q: 為何快取命中不返回 200？**
A: 不一定。如果請求帶有 `If-None-Match` 標頭且 ETag 匹配，返回 304 以節省帶寬。無標頭時返回 200 和內容。

**Q: 為何 POST/PUT 的回應沒有被快取？**
A: `@cache` 只對 GET 生效，其餘方法一律直接執行處理器（仍會輸出 Cache-Control 標頭）。

**Q: 為何同一端點有多個快取項目？**
A: 因為快取金鑰包含查詢參數，而且**不會排序**。`/users?page=1` 和 `/users?page=2`
是不同的快取；`?a=1&b=2` 與 `?b=2&a=1` 也是。

**Q: MemoryBackend 如何在多行程中工作？**
A: 不工作。每個行程有獨立快取，推薦生產環境使用 Redis。

**Q: 快取清除是同步還是非同步？**
A: 異步操作。`await cache.clear_path(...)` 或 `await cache.clear_pattern(...)`。
注意 `clear_pattern()`（以及 `get_all_keys()`、`CacheManager.clear()`）在 Memcached
後端是 no-op，因為 Memcached 協定沒有金鑰列舉能力。
