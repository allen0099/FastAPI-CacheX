# HTTP 快取 {#http-caching}

`@cache` 裝飾器會快取 FastAPI GET 路由的回應，並替你處理 `Cache-Control`、`ETag` 與 `If-None-Match`。本頁說明如何使用它；[快取流程](CACHE_FLOW.md)則說明請求內部發生了什麼。

完整可執行範例（英文）：[`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py)。

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
| `max-age`                | `ttl=N`                                  | :white_check_mark: | `N` 秒內直接回傳已儲存的回應，不執行 handler（`ttl=0` 或未設定：不儲存任何內容）。                                   |
| `no-cache`               | `no_cache=True`                          | :white_check_mark: | 每個請求都執行 handler；回應仍會儲存，`If-None-Match` 相符時回 304。                                            |
| `no-store`               | `no_store=True`                          | :white_check_mark: | 不讀取也不儲存，也不設定 ETag。                                                                                |
| `private`                | `private=True`                           | :white_check_mark: | 完全不經過後端；每個請求都執行 handler，ETag 重新驗證仍有效。                                                   |
| `public`                 | `public=True`                            | :white_check_mark: | 帶有 `Authorization` 的請求仍會使用後端（否則會繞過後端）。                                                    |
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
- **未設定 `ttl`**（`ttl=None`）：與 `private=True` 相同，不從後端讀取，也不寫入。每個請求都會執行 handler，只有當 `If-None-Match` 與新產生的回應相符時才回 304，因此內容變更後，舊的 ETag 永遠不會得到 304
- **使用 `ttl=0`**：送出 `max-age=0`，其餘行為與 `ttl=None` 相同。負數、非 `int`（例如 `1.5` 或 `True`）或超過 `MAX_TTL`（見 [TTL 值](BACKENDS.md#ttl-values)）的 `ttl`，都會在套用裝飾器時以 `CacheXError` 拒絕

只有成功的回應會被儲存。handler *回傳* 非 2xx 狀態的回應（例如 `Response(..., status_code=404)`）會原樣傳出、永不快取，因此暫時性的錯誤不會取代或污染上一筆正常的項目。`206 Partial Content` 同樣排除在外，因為它的本文只對產生它的那個 `Range` 請求有意義。

屬於單一呼叫者的回應同樣不會被儲存（#296）：

- **請求帶有 `Authorization`。** 依照 RFC 9111 §3.5 對共用快取的要求，這類請求會像 `private=True` 一樣繞過後端：不讀取也不寫入，handler 照常執行，`If-None-Match` 與新產生的回應比對。回應（以及 304）會以 `private` 取代 `public` 送出，並保留裝飾器的其他指令（`no_cache` 路由則為 `private, no-cache`），讓 CDN 或代理也不會儲存它。`public=True` 的路由不受此限，設定 `cache_authorized=True` 的路由也一樣；後者是給包含呼叫者身分的 key builder 使用的明確選項（見[需驗證身分的端點](#authenticated-endpoints)）。`must_revalidate=True` 不會解除繞過：RFC 9111 允許共用快取在 `must-revalidate` 下重複使用這類回應，但本函式庫要求明確選擇啟用。
- **handler 自己的 `Cache-Control` 含有 `private` 或 `no-store`**（完整指令，不分大小寫）。回應照常送出但不儲存，而且 handler 的標頭會原樣送出，不會被裝飾器的標頭取代。
- **回應設定了 cookie。** 回應照常送出（包含 `Set-Cookie`），但不儲存；它（以及 304）會以 `private` 取代 `public` 送出並保留其他指令，讓下游的共用快取也不會儲存它。

後兩種情況下，該鍵下已儲存的項目保持不變，而找到有效項目的請求仍會在 handler 執行前由該項目回應。handler 自己的 `private`／`no-store` 標頭一律優先，`no_store=True` 仍只送出 `no-store`。每次略過都會以 `DEBUG` 等級記錄。

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

### 在鍵中加入其他段 {#adding-components-to-the-key}

需要多一個維度（使用者 ID、租戶、語系）的自訂 `key_builder`，應呼叫 `build_cache_key(request, *components)`，而不是自行重組格式。不傳入任何段時，它回傳的正是預設的鍵；每個段會附加在查詢字串之後：

```
{method}|||{host}|||{path}|||{query_params}|||{component}|||...
```

```python
from fastapi import Request

from fastapi_cachex import build_cache_key


def per_tenant_key(request: Request) -> str:
    return build_cache_key(request, request.state.tenant_id)
```

段必須是 `str` 或 `int`（`int` 以十進位寫入，因此 `1` 與 `"1"` 是同一個段）；其他型別，包括 `None`，都會引發 `TypeError`，避免缺少的 ID 悄悄讓所有這類呼叫者共用同一個 `"None"` 鍵。每個段都與 host 和路徑一樣以百分比編碼，因此含有 `|||` 的值無法讓各段錯位。空字串仍是一個段：`build_cache_key(request, "")` 不等於預設的鍵。

由於路徑仍是第三段，`clear_path()` 依然找得到這些鍵：不帶 `include_params` 時，會清除該路徑下查詢字串為空的所有項目，不論其他段為何；帶上它則清除該路徑的所有項目。監控路由會把其他段解碼後列在 `extra_components` 中。`default_key_builder(request)` 就是 `build_cache_key(request)`。

Redis 與 Memcached 後端還會在每個鍵前面加上自己的前綴（預設為 `fastapi_cachex:`），讓其他應用程式可以共用同一台伺服器；`MemoryBackend` 沒有前綴。`CacheManager`（見[應用層快取](APP_CACHE.md)）則使用另一個較簡單、以 `cache:` 為前綴的鍵命名空間，而不是這種以 `|||` 分隔的格式，因為它的鍵與 HTTP 請求無關。

### 依請求標頭區分 {#varying-on-request-headers}

快取鍵不包含任何請求標頭，因此回應內容取決於 `Accept-Language` 等標頭的路由，會把第一個快取下來的語言提供給所有人。請把這類標頭列在 `vary` 中：

```python
@app.get("/greeting")
@cache(ttl=300, vary=["Accept-Language"])
async def greeting(request: Request):
    return {"text": translate("hello", request.headers.get("accept-language"))}
```

每個列出的標頭都會在鍵中加入一個 `name=value` 段：名稱轉為小寫，值去除前後空白（重複的標頭行以 `,` 串接），缺少的標頭視同空值。這些段與鍵的其他部分一樣經過編碼，並接在 `key_builder` 回傳的鍵之後，因此 `vary` 可以與自訂的 key builder 一起使用：`key_builder` 回傳 `build_cache_key(request, "tenant-1")` 時，鍵為 `GET|||example.com|||/greeting|||||||tenant-1|||accept-language=de`。沒有設定 `vary` 的路由，鍵維持不變。

這些名稱也會加入該路由對 GET 請求的每個回應的 `Vary` 標頭，不論是 200 或 304，也不論是否由後端提供（`private`、`no_store`、繞過後端的 `Authorization` 請求，或未儲存的回應），讓應用程式前方的共用快取也依它們區分。回應已列出的名稱（不分大小寫）不會重複加入，帶有 `Vary: *` 的回應則維持原樣。

`vary` 必須是由標頭欄位名稱組成的 list（或 tuple）。套用裝飾器時，會拒絕 `vary="Accept"` 這類單一字串，以及空名稱、`*` 與任何不是有效欄位名稱的值。

> [!WARNING]
> **每個不同的標頭值都是一筆獨立的項目，而這些值來自用戶端。** `Accept-Language: de`、`de-DE`、`de-DE,de;q=0.9` 以及其他寫法都是不同的鍵，用戶端可以在每個請求送出新的值來塞滿後端。每多列一個標頭，項目數量就會成倍增加。若只有少數幾個值有意義，請改在 `key_builder` 中正規化，並自行把該標頭加入 `Vary`：
>
> ```python
> SUPPORTED = ("en", "de", "fr")
>
>
> def locale_key(request: Request) -> str:
>     wanted = request.headers.get("accept-language", "")
>     locale = next(
>         (tag for tag in SUPPORTED if wanted.lower().startswith(tag)), "en"
>     )
>     return build_cache_key(request, locale)
>
>
> @app.get("/greeting")
> @cache(ttl=300, key_builder=locale_key)
> async def greeting(request: Request, response: Response):
>     response.headers["Vary"] = "Accept-Language"
>     return {"text": translate("hello", locale_of(request))}
> ```

`clear_path()` 會清除某個路徑的所有變體（見[在鍵中加入其他段](#adding-components-to-the-key)）。`invalidate()` 接受同樣的 `vary` 清單，只會刪除傳入的請求所選中的那個變體。

### 需驗證身分的端點 {#authenticated-endpoints}

> [!WARNING]
> **預設的快取鍵不包含使用者身分。** 後端由所有 worker 與所有呼叫者共用，因此以預設的 key builder 快取需驗證身分的端點，會把某位使用者的回應提供給下一位存取相同路徑的使用者。
>
> 回應內容取決於請求者身分的端點，請擇一處理：
>
> 1. **`private=True`**：回應永遠不會從共用後端讀取，也不會寫入。`Cache-Control: private` 仍允許使用者自己的瀏覽器快取它，而 `If-None-Match` 重新驗證仍會對新產生的內容運作。
> 2. **包含呼叫者身分的 key builder**：確實需要依使用者區分的伺服器端快取時使用。不要設定 `private`：`private=True` 會繞過後端，key builder 就永遠不會被使用。呼叫者以 `Authorization` 標頭驗證身分時，請傳入 `cache_authorized=True`：沒有它，這類請求同樣會繞過後端。

```python
from fastapi import Request, Response

from fastapi_cachex import build_cache_key
from fastapi_cachex import cache


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
    # 預設的鍵再加上使用者 ID，並與 host、路徑一樣經過編碼。
    return build_cache_key(request, user_id)


@app.get("/me/dashboard")
@cache(ttl=60, key_builder=per_user_key, cache_authorized=True)
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

key builder 只在 `@cache` 讀取或寫入後端時執行，因此 `no_store=True`、`private=True`、沒有 `ttl` 的路由，以及路由未設定 `public=True` 或 `cache_authorized=True` 時帶有 `Authorization` 的請求，都不會呼叫它。0.3.8 以前它仍會被呼叫，但只用於除錯日誌。請讓它不帶副作用。

## 清除快取 {#clearing-the-cache}

### 依路徑或模式 {#by-path-or-pattern}

清除用的方法位於後端上，可以透過 `CacheBackend` 依賴項注入，或以 `BackendProxy.get()` 取得。尚未設定後端時，`CacheBackend` 會註冊與 `@cache` 相同的 `MemoryBackend` 後備後端，因此在任何快取路由執行之前也能使用（0.3.8 之前在那之前會回應 `500`）；`BackendProxy.get()` 則仍會引發 `BackendNotFoundError`。

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

`invalidate(request, key_builder=None, vary=None)` 在項目存在且已移除時回傳 `True`，否則回傳 `False`，包括尚未設定後端的情況。後端本身的錯誤則會拋給呼叫端（見[後端發生錯誤時](#when-the-backend-fails)）。傳入的請求必須能產生快取路由的鍵：相同的方法、主機、路徑與查詢字串。如果快取路由使用自訂的 `key_builder` 或 `vary`，這裡也要傳入相同的值，否則鍵不會相符；使用 `vary` 時，只會刪除請求本身的標頭值所選中的變體，`clear_path()` 則會移除所有變體。

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
- `GET {prefix}/cached-records`：列出每筆快取紀錄的大小、到期時間、`media_type`（儲存的回應的媒體類型，沒有時為 `null`），以及快取內容前 100 個位元組的預覽。設定 `include_content_preview=False` 時，`content_preview` 為 `null`，不會有任何回應本文離開伺服器；鍵、大小與到期時間仍會回報。`content_type` 一律是 `"bytes"`，只為相容而保留；請改讀 `media_type`。

> [!WARNING]
> **這些路由本身沒有任何身分驗證。** `include_in_schema=False` 只是讓它們不出現在 OpenAPI 文件中；任何猜到路徑的人都能讀取。`/cached-records` 含有快取內容的預覽（除非設定 `include_content_preview=False`），並會暴露整個路由結構。正式環境中請務必傳入 `dependencies=[Depends(your_auth)]`，或將它們掛載在僅供內部使用的應用程式上。
>
> 呼叫 `add_routes()` 時若未傳入 `dependencies`，會發出 `UserWarning`。0.4.0 版將要求必須傳入此參數，並將 `include_content_preview` 預設改為關閉（[#298](https://github.com/allen0099/FastAPI-CacheX/issues/298)）。若本機或測試用的應用程式確實要保持開放，請傳入 `dependencies=[]` 明確選擇不設防護，這樣就不會出現警告。

> [!NOTE]
> Memcached 無法列舉鍵，因此在 Memcached 上這兩個路由都不會回傳任何內容。
