# HTTP 快取 {#http-caching}

`@cache` 裝飾器會快取 FastAPI GET 路由的回應，並替你處理 `Cache-Control`、`ETag` 與 `If-None-Match`。本頁說明如何使用它；[快取流程](CACHE_FLOW.md)則說明請求內部發生了什麼。

## `@cache` 裝飾器 {#the-cache-decorator}

```python
from fastapi import FastAPI
from fastapi_cachex import cache

app = FastAPI()


@app.get("/")
@cache(ttl=60)  # 快取 60 秒
async def read_root():
    return {"Hello": "World"}


@app.get("/no-cache")
@cache(no_cache=True)  # 一律重新驗證：每個請求都會執行 handler
async def non_cache_endpoint():
    return {"Hello": "World"}


@app.get("/no-store")
@cache(no_store=True)  # 任何地方都不儲存這個回應
async def non_store_endpoint():
    return {"Hello": "World"}
```

只有 GET 請求會被快取；其他方法照常執行 handler。handler 不需要宣告 `Request` 參數：缺少時裝飾器會自動加上。如果尚未設定任何後端，`@cache` 會改用 `MemoryBackend`（見[後端](BACKENDS.md)）。

## Cache-Control 指令 {#cache-control-directives}

`@cache` 有兩個角色：替瀏覽器與中間層（CDN、反向代理）寫出 `Cache-Control` 標頭，以及在後端維護自己的伺服器端快取。大部分指令只影響前者：它們會寫進標頭，但不論有沒有設定，伺服器端快取的行為都一樣。

| 指令                     | 設定方式                                 | 寫入標頭           | 對伺服器端快取的影響                                                                                           |
|--------------------------|------------------------------------------|--------------------|----------------------------------------------------------------------------------------------------------------|
| `max-age`                | `ttl=N`                                  | :white_check_mark: | `N` 秒內直接回傳已儲存的回應，不執行 handler（`ttl=0` 或未設定：不直接回傳）。                                   |
| `no-cache`               | `no_cache=True`                          | :white_check_mark: | 每個請求都執行 handler；回應仍會儲存，`If-None-Match` 相符時回 304。                                            |
| `no-store`               | `no_store=True`                          | :white_check_mark: | 不讀取也不儲存，也不設定 ETag。                                                                                |
| `private`                | `private=True`                           | :white_check_mark: | 完全不經過後端；每個請求都執行 handler，ETag 重新驗證仍有效。                                                   |
| `public`                 | `public=True`                            | :white_check_mark: | 無（僅寫入標頭）。                                                                                             |
| `immutable`              | `immutable=True`                         | :white_check_mark: | 無（僅寫入標頭）。                                                                                             |
| `must-revalidate`        | `must_revalidate=True`                   | :white_check_mark: | 無（僅寫入標頭）。                                                                                             |
| `stale-while-revalidate` | `stale="revalidate", stale_ttl=N`        | :white_check_mark: | 無（僅寫入標頭）：伺服器端快取不會回傳過期內容。                                                               |
| `stale-if-error`         | `stale="error", stale_ttl=N`             | :white_check_mark: | 無（僅寫入標頭）：handler 失敗時不會改用快取回應。                                                             |
| `s-maxage`               | —                                        | :x:                | —                                                                                                              |
| `proxy-revalidate`       | —                                        | :x:                | —                                                                                                              |
| `no-transform`           | —                                        | :x:                | —                                                                                                              |
| `must-understand`        | —                                        | :x:                | —                                                                                                              |

`no_cache=True` 與 `no_store=True` 會取代標頭的其餘內容：使用 `no_cache` 時只送出 `no-cache`（若有設定，再加上 `must-revalidate`），使用 `no_store` 時只送出 `no-store`。其他參數如何組合，請見[快取流程](CACHE_FLOW.md#2-cache-control-directives)。

### 請求的 `Cache-Control` 會被忽略 {#the-requests-cache-control-is-ignored}

用戶端自己送出的 `Cache-Control` 請求標頭（例如瀏覽器強制重新整理時送出的 `no-cache`、`max-age=0` 等）不會改變 `@cache` 的行為。這是刻意的設計：若請求標頭能繞過快取，任何用戶端都能讓每個請求直接打到你的 handler。條件式請求仍會處理：`If-None-Match` 相符時回 304。

## 快取命中時的行為 {#cache-hit-behavior}

當快取項目仍有效（在 TTL 內）時：

- **預設行為**：直接回傳快取的內容，連同 handler 當初產生的狀態碼與標頭，不會重新執行端點的 handler
- **帶有 `If-None-Match` 標頭**：ETag 相符時回傳 HTTP 304 Not Modified
- **使用 `no-cache` 指令**：先以新產生的內容強制重新驗證，再決定是否回 304
- **使用 `private=True`**：不從共用後端讀取，也不寫入；每次都執行 handler，只有 `If-None-Match` 重新驗證有效
- **未設定 `ttl`**（`ttl=None`）：快取的回應本文永遠不會直接回傳；每個請求都會執行 handler，唯一的例外是 `If-None-Match` 與已儲存 ETag 相符的請求，會得到 304
- **使用 `ttl=0`**：送出 `max-age=0`，其餘行為與 `ttl=None` 相同。負數、非 `int`（例如 `1.5` 或 `True`）或超過 `MAX_TTL`（見 [TTL 值](BACKENDS.md#ttl-values)）的 `ttl`，都會在套用裝飾器時以 `CacheXError` 拒絕

只有成功的回應會被儲存。handler *回傳* 非 2xx 狀態的回應（例如 `Response(..., status_code=404)`）會原樣傳出、永不快取，因此暫時性的錯誤不會取代或污染上一筆正常的項目。`206 Partial Content` 同樣排除在外，因為它的本文只對產生它的那個 `Range` 請求有意義。`Set-Cookie` 永遠不會被儲存或重播。

handler 回傳一般資料而非 `Response` 時，得到的處理與沒有 `@cache` 時相同：回傳值會經過路由的 response model 驗證與過濾（明確宣告的，或由回傳型別註記推斷，並套用 `response_model_*` 選項），套用路由的 `status_code`，而在注入的 `response: Response` 參數上設定的狀態碼與標頭也會保留。

### 後端發生錯誤時 {#when-the-backend-fails}

`@cache` 採取 fail open。讀取時後端拋出錯誤（例如 Redis 或 Memcached 無法連線），該請求會被當成快取未命中，照常執行 handler。儲存回應時拋出錯誤（例如回應超過 Memcached 的項目大小上限，預設為 1 MB），回應會照常送出，只是不會被儲存。兩種情況都會在 `fastapi_cachex.cache` logger 記錄一則警告，因此後端中斷不會讓有快取的路由變成 500；負載會轉到你的 handler 上，請留意這些警告。

傳入 `fail_open=False` 則會讓後端錯誤直接往外拋出，使該請求失敗：

```python
@app.get("/report")
@cache(ttl=300, fail_open=False)
async def report():
    return await build_report()
```

這只適用於 `@cache`。`invalidate()`、`CacheManager`、`StateManager`、`CacheLock` 與 Session 仍會把後端錯誤拋給呼叫端。

## 快取鍵 {#cache-keys}

快取鍵以下列格式產生，以避免衝突：

```
{method}|||{host}|||{path}|||{query_params}
```

這可確保：

- 不同的 HTTP 方法（GET、POST 等）不共用快取
- 不同的主機不共用快取（適用於多租戶情境）
- 不同的查詢參數各有獨立的快取項目
- 同一個端點搭配不同參數時可以各自快取

查詢參數依用戶端送出的順序取用，不會排序，因此 `?a=1&b=2` 與 `?b=2&a=1` 對同一個邏輯上的請求而言是兩筆不同的快取項目。

host 與路徑來自用戶端，因此其中的 `|` 與 `%` 會以百分比編碼寫入（`%7C` 與 `%25`）。含有 `|||` 的 `Host` 標頭或路徑因此無法讓各段錯位，使某個請求的快取鍵與另一個請求相同。查詢字串本來就經過 URL 編碼。`clear_path()` 接受應用程式看到的路徑（`request.url.path`），並以同樣方式編碼；`clear_pattern()` 比對的是儲存的快取鍵，所以在模式中要把 `|` 寫成 `%7C`。0.3.8 之前兩者都照原樣儲存，因此升級後，host 或路徑含有 `|` 或 `%` 的項目會重新快取一次。

host 仍是用戶端送來的任何值。除非應用程式前方的反向代理或負載平衡器已會拒絕未知的 host，否則請加上 Starlette 的 `TrustedHostMiddleware`，讓偽造的 `Host` 得到 `400`，而不是在快取中塞滿沒有其他人會請求的項目：

```python
from starlette.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["example.com", "*.example.com"]
)
```

所有後端都會自動替鍵加上前綴（例如 `fastapi_cachex:`）作為命名空間，以避免與其他應用程式衝突。`CacheManager`（見[應用層快取](APP_CACHE.md)）則使用另一個較簡單、以 `cache:` 為前綴的鍵命名空間，而不是這種以 `|||` 分隔的格式，因為它的鍵與 HTTP 請求無關。

### 需驗證身分的端點 {#authenticated-endpoints}

> [!WARNING]
> **預設的快取鍵不包含使用者身分。** 後端由所有 worker 與所有呼叫者共用，因此以預設的 key builder 快取需驗證身分的端點，會把某位使用者的回應提供給下一位存取相同路徑的使用者。
>
> 回應內容取決於請求者身分的端點，請擇一處理：
>
> 1. **`private=True`**：回應永遠不會從共用後端讀取，也不會寫入。`Cache-Control: private` 仍允許使用者自己的瀏覽器快取它，而 `If-None-Match` 重新驗證仍會對新產生的內容運作。
> 2. **包含呼叫者身分的 key builder**：確實需要依使用者區分的伺服器端快取時使用。不要設定 `private`：`private=True` 會繞過後端，key builder 就永遠不會被使用。

```python
from fastapi import Request, Response

from fastapi_cachex import cache
from fastapi_cachex.types import CACHE_KEY_SEPARATOR
from fastapi_cachex.types import escape_key_component


# 1. 完全不放進共用快取。
@app.get("/me/profile")
@cache(ttl=60, private=True)
async def my_profile(user: CurrentUser):
    return user.profile


# 2. 或讓每位使用者擁有自己的項目。
def per_user_key(request: Request) -> str:
    # `request.state.user_id` 由你的驗證層在確認呼叫者身分後填入；
    # 絕對不要直接從未經驗證的請求標頭讀取身分（見下方說明）。
    user_id = getattr(request.state, "user_id", "anonymous")
    return (
        f"{request.method}{CACHE_KEY_SEPARATOR}"
        f"{escape_key_component(request.headers.get('host', 'unknown'))}"
        f"{CACHE_KEY_SEPARATOR}"
        f"{escape_key_component(request.url.path)}{CACHE_KEY_SEPARATOR}"
        f"{request.query_params}{CACHE_KEY_SEPARATOR}{user_id}"
    )


@app.get("/me/dashboard")
@cache(ttl=60, key_builder=per_user_key)
async def my_dashboard(user: CurrentUser, response: Response):
    # 沒有 `private` 時，回應會帶著 `Cache-Control: max-age=60` 送出，
    # 共用快取（CDN、反向代理）可能會儲存它。對承載身分的標頭設定 Vary，
    # 讓這類快取為每位使用者各保留一份。
    response.headers["Vary"] = "Authorization"
    return build_dashboard(user)
```

依使用者區分的項目要不被應用程式前方的共用快取交給其他使用者，前提是這些快取會遵守該標頭的 `Vary`。若它們不遵守，或身分來自共用快取看不到的地方，請改用做法 1。

> [!CAUTION]
> key builder 決定了誰能看到誰的資料，因此它讀取的身分必須來自已經驗證過的來源：已檢查權杖中的 claim、你的依賴項解析出的使用者，或驗證中介軟體寫入 `request.state` 的值。
>
> ```python
> # ❌ 絕對不要這樣做：任何人都能送出這個標頭。
> user_id = request.headers.get("x-user-id", "anonymous")
> ```
>
> 以原始請求標頭組成的鍵等同於水平權限提升：送出 `X-User-Id: <someone-else>` 就會拿到該使用者的快取回應。

## 清除快取 {#clearing-the-cache}

### 依路徑或模式 {#by-path-or-pattern}

清除用的方法位於後端上，可以透過 `CacheBackend` 依賴項注入，或以 `BackendProxy.get()` 取得：

```python
from fastapi_cachex import CacheBackend


@app.post("/admin/clear")
async def clear(cache: CacheBackend) -> None:
    # 清除特定路徑：只清除「沒有」查詢參數的項目……
    await cache.clear_path("/api/users")
    # ……或連同所有查詢參數的變體一起清除
    await cache.clear_path("/api/users", include_params=True)

    # 依模式清除：比對整個鍵 method|||host|||path|||query
    await cache.clear_pattern("GET|||*|||/api/users/*")
    # 你自己組成的鍵（例如 CacheManager 的鍵）可以直接比對
    await cache.clear_pattern("cache:user:*")

    # 清除全部
    await cache.clear()  # 移除所有快取項目
```

`clear_path()` 會比對該路徑在所有方法與主機下的項目。只寫成路徑的模式（例如 `clear_pattern("/api/users/*")`）無法比對到 HTTP 鍵；這類呼叫沒有清除任何項目時，會發出 `RuntimeWarning`，提示你改用 `clear_path()`。

各後端支援的功能列於[後端](BACKENDS.md)。

### 使單一快取路由失效 {#invalidating-a-single-cached-route}

`clear_path`/`clear_pattern` 作用於一整批鍵。若只想刪除某個 `@cache` 裝飾路由會用到的那一筆項目（通常是在資料變更之後），請呼叫 `invalidate()`，它會以相同的 key builder 重建該路由的鍵並刪除：

```python
from fastapi import Request
from starlette.requests import Request as StarletteRequest

from fastapi_cachex import cache, invalidate


@app.get("/items/{item_id}")
@cache(ttl=300)
async def read_item(item_id: int):
    return await load(item_id)


@app.post("/items/{item_id}")
async def update_item(item_id: int, request: Request):
    await save(item_id)
    # 組出快取的 GET 會使用的鍵：相同的主機與標頭、
    # GET 方法、快取的路徑、沒有查詢字串。
    scope = dict(request.scope)
    scope["method"] = "GET"
    scope["path"] = f"/items/{item_id}"
    scope["query_string"] = b""
    return {"invalidated": await invalidate(StarletteRequest(scope))}
```

`invalidate(request, key_builder=None)` 在項目存在且已移除時回傳 `True`，否則回傳 `False`（包括尚未設定後端的情況；它永遠不會拋出例外）。傳入的請求必須能產生快取路由的鍵：相同的方法、主機、路徑與查詢字串。如果快取路由使用自訂的 `key_builder`，這裡也要傳入同一個，否則鍵不會相符。

## 監控路由 {#monitoring-routes}

`add_routes()` 會掛載兩個唯讀端點，回報後端目前的內容：

```python
from fastapi import Depends, FastAPI
from fastapi_cachex import add_routes

app = FastAPI()
add_routes(
    app,
    prefix="/admin/cache",  # 預設 "" -> /cached-hits、/cached-records
    include_in_schema=False,  # 預設：不出現在 OpenAPI 中
    dependencies=[Depends(verify_admin)],
    include_content_preview=False,  # 預設 True：顯示前 100 個位元組
)
```

- `GET {prefix}/cached-hits`：列出每筆快取項目，拆分為方法、主機、路徑與查詢，附上 ETag 與到期時間，另外統計有效與已過期的項目數，以及不重複的快取路徑。它不會計算命中次數。
- `GET {prefix}/cached-records`：列出每筆快取紀錄的大小、到期時間，以及快取內容前 100 個位元組的預覽。設定 `include_content_preview=False` 時，`content_preview` 為 `null`，不會有任何回應本文離開伺服器；鍵、大小與到期時間仍會回報。

> [!WARNING]
> **這些路由本身沒有任何身分驗證。** `include_in_schema=False` 只是讓它們不出現在 OpenAPI 文件中；任何猜到路徑的人都能讀取。`/cached-records` 含有快取內容的預覽（除非設定 `include_content_preview=False`），並會暴露整個路由結構。正式環境中請務必傳入 `dependencies=[Depends(your_auth)]`，或將它們掛載在僅供內部使用的應用程式上。

> [!NOTE]
> Memcached 無法列舉鍵，因此在 Memcached 上這兩個路由都不會回傳任何內容。
