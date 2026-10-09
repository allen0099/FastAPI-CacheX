# 從 fastapi-cache2 遷移 {#migrating-from-fastapi-cache2}

[fastapi-cache2](https://github.com/long2ice/fastapi-cache)（以 `fastapi_cache` 匯入）是安裝數最多的 FastAPI 快取，最新版本 0.2.2 發行於 2024 年 7 月。本頁把它的 API 對應到 FastAPI-CacheX，列出行為上的差異，並指出沒有對應功能的部分。內容以 fastapi-cache2 0.2.2 與 FastAPI-CacheX 0.5.0 為準。如果你還在考慮是否要轉換，請見[何時使用](COMPARISON.md)。

FastAPI-CacheX 需要 Python 3.10 以上與 FastAPI 0.128.2 以上。

## 同一個路由，遷移前後 {#before-and-after}

使用 fastapi-cache2：

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from fastapi_cache.decorator import cache
from redis import asyncio as aioredis


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    redis = aioredis.from_url("redis://localhost")
    FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/items/{item_id}")
@cache(expire=60)
async def read_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}
```

使用 FastAPI-CacheX（`uv add "fastapi-cachex[redis]"`）：

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fastapi_cachex import BackendProxy, cache
from fastapi_cachex.backends import AsyncRedisCacheBackend


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    backend = AsyncRedisCacheBackend(host="localhost", key_prefix="fastapi-cache:")
    BackendProxy.set(backend)
    try:
        yield
    finally:
        BackendProxy.set(None)
        await backend.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/items/{item_id}")
@cache(ttl=60)
async def read_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}
```

兩者的裝飾器順序相同：路由裝飾器在前，`@cache` 緊接在函式上方。完整的 Redis 設定請見[後端](BACKENDS.md#redis)。

## API 對照 {#api-mapping}

| fastapi-cache2 | FastAPI-CacheX | 說明 |
|----------------|----------------|------|
| `FastAPICache.init(backend, prefix=...)` | `BackendProxy.set(backend)` | 前綴是後端的 `key_prefix`（預設 `fastapi_cachex:`）。沒有設定後端時，`@cache` 會退回使用 `MemoryBackend` 並記錄警告。 |
| `InMemoryBackend()` | `MemoryBackend()` | `MemoryBackend(max_entries=...)` 以 LRU 淘汰限制項目數量。 |
| `RedisBackend(redis)` | `AsyncRedisCacheBackend(host=..., port=..., password=..., db=...)` | 它依這些設定自行建立 client，而不是接收現成的 client。 |
| `MemcachedBackend(aiomcache.Client(...))` | `MemcachedBackend(servers=["host:port"])` | 使用 `pymemcache`（`memcached` extra）。Memcached 無法列舉鍵，所以依路徑或模式清除在它上面沒有作用。 |
| `DynamoBackend` | — | 沒有 DynamoDB 後端。 |
| `@cache(expire=60)` | `@cache(ttl=60)` | `ttl` 也接受 `timedelta`。沒有 `ttl` 時不會儲存任何東西。 |
| `FastAPICache.init(expire=...)` | — | `@cache` 沒有全域預設值，請為每個路由指定 `ttl`。應用層快取可用 `CacheManager(default_ttl=...)` 設定預設值。 |
| `@cache(namespace="items")` | — | 快取鍵由請求產生（見[快取鍵](#keys)）；改以路徑或模式清除一組路由。 |
| `@cache(key_builder=f)` | `@cache(key_builder=f)` | 函式只接收 `Request`，回傳 `str`。請用 `build_cache_key(request, *components)` 建立（見[在鍵中加入其他段](HTTP_CACHING.md#adding-components-to-the-key)）。 |
| `@cache(coder=...)`、`JsonCoder`、`PickleCoder` | — | 直接儲存產生好的回應本文（見[儲存方式](#storage)）。 |
| `FastAPICache.clear(namespace=...)` | `await backend.clear_path(path, include_params=True)` 或 `clear_pattern(...)` | 在 `CacheBackend` 依賴項或 `BackendProxy.get()` 取得的後端上呼叫。見[清除快取](HTTP_CACHING.md#clearing-the-cache)。 |
| `FastAPICache.clear(key=...)` | `await invalidate(request)` | 依請求重建快取鍵並刪除該項目。 |
| `FastAPICache.clear()` | `await backend.clear()` | 在 Memcached 上會清空整台伺服器。 |
| `X-FastAPI-Cache: HIT`／`MISS`（`cache_status_header=`） | `@cache(debug_header=True)` 送出 `X-Cache: HIT`、`MISS` 或 `BYPASS` | 預設關閉。 |
| 用在非端點函式上的 `@cache` | `@cached(ttl=60)` 或 `CacheManager.get_or_set()` | 和 fastapi-cache2 一樣以引數作為鍵。見[快取一個函式](APP_CACHE.md#caching-a-function)。 |
| `FastAPICache.init(enable=False)` | — | 沒有關閉快取的開關。測試時，請為每個測試設定新的 `MemoryBackend`。 |

## 行為上的差異 {#behaviour-that-differs}

### 快取鍵來自請求，而不是引數 {#keys}

fastapi-cache2 以函式的模組、名稱與引數計算雜湊。FastAPI-CacheX 以請求作為鍵：`http:v2|method|host|path|query`，查詢參數會排序。實際上：

- 服務同一個應用程式的兩個主機會得到不同的項目；被 FastAPI 解析成相同引數的兩個查詢字串（`?page=1` 與 `?page=01`）也一樣。
- 回應取決於某個請求標頭時，快取鍵必須包含該標頭：`@cache(vary=["Accept-Language"])` 或自訂 `key_builder`。除非該標頭是函式引數，否則在 fastapi-cache2 中同樣需要自訂 key builder。
- 不會讀取 fastapi-cache2 留下的鍵。轉換後的第一個請求會是快取未命中；舊的鍵會依它們的 TTL 自行過期，也可以依舊的前綴清除。

### 帶有憑證的請求會略過快取 {#credentials}

fastapi-cache2 對帶 `Authorization` 標頭的請求和其他請求一樣快取，所以每個使用者各自不同的端點必須自行把使用者放進快取鍵。FastAPI-CacheX 對帶 `Authorization` 或非空 `request.session` 的請求，不讀取也不寫入共用後端，並以 `Cache-Control: private` 回應。如果你原本依賴 fastapi-cache2 快取這類路由：

- 回應對每個使用者都相同時，設定 `@cache(ttl=60, public=True)`。
- 回應依使用者而不同時，設定 `cache_authorized=True`，並搭配把驗證過的使用者放進快取鍵的 `key_builder`。見[帶有憑證的請求](HTTP_CACHING.md#requests-with-credentials)。

設定 cookie 的路由也永遠不會被儲存。

### 忽略用戶端的 `Cache-Control` {#request-cache-control}

fastapi-cache2 遇到帶 `Cache-Control: no-store` 的請求會略過快取，遇到 `no-cache` 則重新產生回應。FastAPI-CacheX 忽略請求的 `Cache-Control`，所以沒有任何用戶端能把每個請求都直接送到你的 handler。相符的 `If-None-Match` 仍會得到 `304`。

### 儲存方式 {#storage}

fastapi-cache2 以 coder 儲存回傳值（預設 JSON，可選 pickle），讀取時再解碼回端點的回傳型別註記。FastAPI-CacheX 儲存 FastAPI 產生的回應：本文位元組、狀態碼、媒體類型與標頭，包在一個 JSON 結構中。不使用 pickle，所以從共用 Redis 讀出的項目不會執行程式碼；任何回應類別都能快取，包括 `HTMLResponse` 與 `PlainTextResponse`。`StreamingResponse` 或 `FileResponse` 會照常送出，但不會被儲存。

應用層快取（`CacheManager`、`@cached`）儲存的是 JSON 值；讀回來的內容請見 [JSON 往返](APP_CACHE.md#json-round-trip)。

### 標頭 {#headers}

兩者都會送出 `Cache-Control: max-age` 與弱 `ETag`，並在 `If-None-Match` 相符時回 `304`。FastAPI-CacheX 也會依裝飾器的參數寫出其他指令（`no_cache`、`no_store`、`private`、`public`、`immutable`、`must_revalidate`、`stale`），命中時送出 `Age`，並以快取的 `GET` 回應 `HEAD`。handler 不需要為此宣告 `Response` 參數。見 [Cache-Control 指令](HTTP_CACHING.md#cache-control-directives)。

### 只儲存 GET {#methods}

兩者都只快取 `GET`。在同時接受 `HEAD` 的路由上，FastAPI-CacheX 會以 `GET` 的項目回應 `HEAD`。

## 沒有對應功能的部分 {#no-equivalent}

- DynamoDB 後端。
- `namespace=` 以及依命名空間清除。請改以路徑、模式或 `invalidate()` 清除。
- Coder，以及把快取值解碼回回傳型別註記。
- 全域預設的 `expire`，以及 `enable=False`。
- Python 3.8 與 3.9，以及早於 0.128.2 的 FastAPI。
