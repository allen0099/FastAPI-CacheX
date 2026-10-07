# HTTP 快取 {#http-caching}

`@cache` 裝飾器會快取 FastAPI GET 路由的回應（並以它們回應 HEAD），並替你處理 `Cache-Control`、`ETag` 與 `If-None-Match`。本頁說明如何使用它；[快取流程](CACHE_FLOW.md)則說明請求內部發生了什麼。

完整可執行範例（英文）：[`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py)。

## `@cache` 裝飾器 {#the-cache-decorator}

以下是 [`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py) 的路由（程式碼註解為英文）：

<!-- fmt:off -->
```python
--8<-- "examples/http_cache.py:routes"
```
<!-- fmt:on -->

`ttl=60` 會在 60 秒內提供儲存的回應，`no_cache=True` 讓用戶端每次都重新驗證，`private=True` 則讓回應不存入共用的後端。`no_store=True` 讓回應不存入任何快取；所有選項列在 [Cache-Control 指令](#cache-control-directives)。

只有 GET 請求會被快取，HEAD 則以 GET 的項目回應（見 [HEAD 請求](#head-requests)）；其他方法照常執行 handler。handler 不需要宣告 `Request` 參數：缺少時裝飾器會自動加上。如果尚未設定任何後端，`@cache` 會改用 `MemoryBackend`，並在每個行程記錄一次警告（見[後端](BACKENDS.md#in-memory-default)）。

### 裝飾器順序 {#decorator-order}

`@cache` 必須寫在路由裝飾器（`@app.get(...)`、`@router.get(...)`）的**下方**，緊貼在函式上方。Python 由下而上套用裝飾器，而 FastAPI 註冊的是傳到路由裝飾器的那個函式。若 `@cache` 寫在上方，FastAPI 註冊的是未經裝飾的 handler，永遠不會呼叫快取包裝函式：路由照常運作，但什麼都不會被快取，不會送出 `Cache-Control` 或 `ETag` 標頭，也不會有任何警告。

```python
# ✅ 會快取：FastAPI 註冊的是 @cache 的包裝函式。
@app.get("/items")
@cache(ttl=60)
async def items(): ...


# ❌ 不會快取：FastAPI 註冊的是原本的函式，包裝函式永遠不會被呼叫。
@cache(ttl=60)
@app.get("/items")
async def items(): ...
```

`app.add_api_route(path, cache(ttl=60)(handler))` 也是一樣：傳入裝飾後的函式，而不是原本的函式。

## Cache-Control 指令 {#cache-control-directives}

`@cache` 有兩個角色：替瀏覽器與中間層（CDN、反向代理）寫出 `Cache-Control` 標頭，以及在後端維護自己的伺服器端快取。大部分指令只影響前者：它們會寫進標頭，但不論有沒有設定，伺服器端快取的行為都一樣。

| 指令                     | 設定方式                                 | 寫入標頭           | 對伺服器端快取的影響                                                                                           |
|--------------------------|------------------------------------------|--------------------|----------------------------------------------------------------------------------------------------------------|
| `max-age`                | `ttl=N`                                  | :white_check_mark: | `N` 秒內直接回傳已儲存的回應，不執行 handler（`ttl=0` 或未設定：不儲存任何內容）。                                   |
| `no-cache`               | `no_cache=True`                          | :white_check_mark: | 每個請求都執行 handler；回應仍會儲存，`If-None-Match` 相符時回 304。                                            |
| `no-store`               | `no_store=True`                          | :white_check_mark: | 不讀取也不儲存，也不設定 ETag。                                                                                |
| `private`                | `private=True`                           | :white_check_mark: | 完全不經過後端；每個請求都執行 handler，ETag 重新驗證仍有效。                                                   |
| `public`                 | `public=True`                            | :white_check_mark: | 帶有 `Authorization` 或 Session 的請求仍會使用後端（否則會繞過後端）。                                         |
| `immutable`              | `immutable=True`                         | :white_check_mark: | 無（僅寫入標頭）。                                                                                             |
| `must-revalidate`        | `must_revalidate=True`                   | :white_check_mark: | 無（僅寫入標頭）。                                                                                             |
| `stale-while-revalidate` | `stale="revalidate", stale_ttl=N`        | :white_check_mark: | 無（僅寫入標頭）：伺服器端快取不會回傳過期內容。                                                               |
| `stale-if-error`         | `stale="error", stale_ttl=N`             | :white_check_mark: | 無（僅寫入標頭）：handler 失敗時不會改用快取回應。                                                             |
| `s-maxage`               | —                                        | :x:                | —                                                                                                              |
| `proxy-revalidate`       | —                                        | :x:                | —                                                                                                              |
| `no-transform`           | —                                        | :x:                | —                                                                                                              |
| `must-understand`        | —                                        | :x:                | —                                                                                                              |

`no_cache=True` 與 `no_store=True` 會取代標頭的其餘內容：使用 `no_cache` 時只送出 `no-cache`（若有設定，再加上 `must-revalidate`），使用 `no_store` 時只送出 `no-store`。其他參數如何組合，請見[快取流程](CACHE_FLOW.md#2-cache-control-directives)。

`no_store=True` 搭配其他任何快取參數（`ttl`、`stale`、`no_cache`、`public`、`private`、`immutable`、`must_revalidate`）時，套用裝飾器時 `@cache` 會發出指向你 `@cache(...)` 那一行的 `UserWarning`：`no_store` 會覆蓋這些參數，警告會列出它們。

不帶參數的 `@cache()`，既沒有 `ttl` 也沒有任何指令，不會儲存任何內容，也沒有自己的 `Cache-Control`。它會加上 ETag，以 `304` 回應相符的 `If-None-Match`，並保留 handler 自己的 `Cache-Control`（沒有就不送）。與其他路由一樣，設定 Cookie 的回應，或回應帶有 `Authorization` 或 Session 的請求時，仍會送出 `private`。

### 請求的 `Cache-Control` 會被忽略 {#the-requests-cache-control-is-ignored}

用戶端自己送出的 `Cache-Control` 請求標頭（例如瀏覽器強制重新整理時送出的 `no-cache`、`max-age=0` 等）不會改變 `@cache` 的行為。這是刻意的設計：若請求標頭能繞過快取，任何用戶端都能讓每個請求直接打到你的 handler。條件式請求仍會處理：`If-None-Match` 相符時回 304。

## 快取命中時的行為 {#cache-hit-behavior}

當快取項目仍有效（在 TTL 內）時：

- **預設行為**：直接回傳快取的內容，連同 handler 當初產生的狀態碼與標頭（重複送出的標頭會保留每一行），不會重新執行端點的 handler
- **帶有 `If-None-Match` 標頭**：ETag 相符時回傳 HTTP 304 Not Modified
- **使用 `no-cache` 指令**：先以新產生的內容強制重新驗證，再決定是否回 304
- **使用 `private=True`**：不從共用後端讀取，也不寫入；每次都執行 handler，只有 `If-None-Match` 重新驗證有效
- **未設定 `ttl`**（`ttl=None`）：與 `private=True` 相同，不從後端讀取，也不寫入。每個請求都會執行 handler，只有當 `If-None-Match` 與新產生的回應相符時才回 304，因此內容變更後，舊的 ETag 永遠不會得到 304
- **使用 `ttl=0`**：送出 `max-age=0`，其餘行為與 `ttl=None` 相同。負數、非 `int`（例如 `1.5` 或 `True`）或超過 `MAX_TTL`（見 [TTL 值](BACKENDS.md#ttl-values)）的 `ttl`，都會在套用裝飾器時以 `CacheXError` 拒絕
- **使用 `timedelta`**：`ttl=timedelta(minutes=5)` 等同於 `ttl=300`，`stale_ttl` 也一樣；帶有小數秒的 `timedelta` 會像 `float` 一樣被拒絕

### `Age` 標頭 {#the-age-header}

由已儲存項目回應的回應會帶有 `Age` 標頭：從 `@cache` 儲存它起經過的整數秒數（RFC 9111 §5.1）。這包括快取命中，以及依已儲存項目的 ETag 回應的 304。`Cache-Control` 仍然是 `max-age=<ttl>`，瀏覽器或 CDN 會從中扣掉 `Age`（RFC 9111 §4.2.3），因此在 60 秒 ttl 的第 50 秒時送出的回應，下游最多只會再重複使用 10 秒。沒有 `Age` 時，在項目即將過期前的命中會讓下游重新計時，內容最多可能被重複使用到 ttl 的兩倍。

```
GET /items  → 200, Cache-Control: max-age=60，沒有 Age（handler 有執行）
GET /items  → 200, Cache-Control: max-age=60, Age: 42（儲存後 42 秒送出）
```

項目的儲存時間取自儲存它的行程的系統時鐘，而由送出它的行程讀取，因此 `Age` 會限制在 `0`–`ttl` 之間，以防兩台主機的時鐘不一致。handler 有執行時（未命中、`no_cache=True`、繞過後端的請求）不會送出 `Age`；0.3.9 之前的版本儲存的項目沒有記錄時間，也不會送出。handler 自己設定的 `Age` 標頭不會被儲存。

只有成功的回應會被儲存。handler *回傳* 非 2xx 狀態的回應（例如 `Response(..., status_code=404)`）會原樣傳出、永不快取，因此暫時性的錯誤不會取代或污染上一筆正常的項目。`206 Partial Content` 同樣排除在外，因為它的本文只對產生它的那個 `Range` 請求有意義。

屬於單一呼叫者的回應同樣不會被儲存（#296）：

- **請求帶有 `Authorization` 或 Session。** 依照 RFC 9111 §3.5 對共用快取的要求，這類請求會像 `private=True` 一樣繞過後端：不讀取也不寫入，handler 照常執行，`If-None-Match` 與新產生的回應比對。回應（以及 304）會以 `private` 取代 `public` 送出，並保留裝飾器的其他指令（`no_cache` 路由則為 `private, no-cache`），讓 CDN 或代理也不會儲存它。`public=True` 的路由不受此限。設定 `cache_authorized=True`（給包含呼叫者身分的 key builder 使用的明確選項，見[需驗證身分的端點](#authenticated-endpoints)）的路由會為這類請求讀寫後端，但回應仍帶有 `private`：項目只在後端依呼叫者區分，CDN 則只以 URL 為鍵（0.3.9 之前會原樣送出裝飾器的標頭，#372）。`must_revalidate=True` 不會解除繞過：RFC 9111 允許共用快取在 `must-revalidate` 下重複使用這類回應，但本函式庫要求明確選擇啟用。沒有正數 `ttl` 的路由本來就不經過後端，但它對這類請求的回應仍會加上 `private`（0.3.9 之前不會加，#362）；`private=True` 的路由本來就會送出 `private`。請求「帶有 Session」是指 `FastAPICacheXSessionMiddleware`（已棄用，0.5.0 移除）為它載入了 Session（權杖來自標頭、Bearer 權杖或 Session Cookie 皆可，有沒有使用者都算），或在任何 Session 中介軟體（包括 Starlette 的）下 `request.session` 不是空的。解析不出 Session 的權杖（偽造、過期）不算，因此無法用來略過快取。0.3.9 之前只有 `Authorization` 會觸發繞過，讀取 Session 的路由只加上 `@cache` 時，會把一位訪客的回應提供給下一位（#319）。會讀取後端的路由第一次繞過時，會以 `WARNING` 等級記錄（見[帶有憑證的請求](#requests-with-credentials)）。
- **handler 自己的 `Cache-Control` 含有 `private` 或 `no-store`**（完整指令，不分大小寫）。回應照常送出但不儲存，而且 handler 的標頭會原樣送出，不會被裝飾器的標頭取代。
- **回應設定了 cookie。** 回應照常送出（包含 `Set-Cookie`），但不儲存；它（以及 304）會以 `private` 取代 `public` 送出並保留其他指令，讓下游的共用快取也不會儲存它。針對新產生回應的 304 同樣帶有 `Set-Cookie`，handler 的背景任務也照常執行（0.4.1 之前，繞過後端的請求與 `no_cache` 路由的 304 會遺失兩者，#233）。

後兩種情況下，該鍵下已儲存的項目保持不變，而找到有效項目的請求仍會在 handler 執行前由該項目回應。handler 自己的 `private`／`no-store` 標頭一律優先，`no_store=True` 仍只送出 `no-store`。每次略過都會以 `DEBUG` 等級記錄。

handler 回傳一般資料而非 `Response` 時，得到的處理與沒有 `@cache` 時相同：回傳值會經過路由的 response model 驗證與過濾（明確宣告的，或由回傳型別註記推斷，並套用 `response_model_*` 選項），套用路由的 `status_code`，而在注入的 `response: Response` 參數上設定的狀態碼與標頭也會保留。

依賴項在 FastAPI 共用的 `Response` 上設定的標頭與 cookie，會以本次請求的值送到用戶端，未命中、命中與 304 皆然，不論 handler 是否宣告 `response: Response`。它們不會隨項目儲存，因此 `X-RateLimit-Remaining` 這類標頭不會重播寫入快取那次請求的值。依賴項帶有 `private` 或 `no-store` 的 `Cache-Control`，以及依賴項設定的 cookie，都與 handler 自己設定的視為相同：回應不會儲存，並以依賴項的標頭或 `private` 送出，命中與 304 也一樣。依賴項其他的 `Cache-Control` 與 handler 自己的視為相同：裝飾器的會取代它，不帶參數的 `@cache()` 則保留它。依賴項設定的狀態碼會送出，但回應不會寫入後端，因為它可能只適用於該次請求；命中時沿用儲存的狀態碼。handler 自己加到 `response` 上的標頭，以及它設定的狀態碼，則屬於儲存的回應；handler 設定了依賴項也設定過的標頭時，送出的是 handler 的值，命中時也一樣。handler 刪除依賴項的標頭，只在它執行時才有效。FastAPI 本身只在 handler 回傳一般資料時合併這些標頭；`@cache` 也會把依賴項的標頭加到 handler 自己回傳的 `Response` 上。0.4.1 之前，沒有宣告 `response: Response` 參數的 handler 會遺失它們，有宣告的則會重播寫入快取那次請求的標頭（#233）。

> [!NOTE]
> 因此，每次請求都設定 cookie 的依賴項（例如套用到整個應用程式的 CSRF 或 session 更新依賴項），會讓它套用到的每個 `@cache` 路由都不寫入後端。請只把它套用到需要的路由，或只在 cookie 改變時才設定。

### HEAD 請求 {#head-requests}

接受 HEAD 的路由會得到與 GET 相同的處理（#253）。`@app.get` 只註冊 GET，因此要列出兩個方法：

```python
@app.api_route("/items", methods=["GET", "HEAD"])
@cache(ttl=60)
async def list_items() -> list[str]: ...
```

HEAD 請求會讀取 GET 以同一個快取鍵儲存的項目，這是 RFC 9110 §9.3.2 所允許的：命中時它會得到儲存的狀態碼與標頭、`ETag`、`Age` 以及儲存的本文的 `Content-Length`，不會執行 handler；相符的 `If-None-Match` 則得到 304。快取鍵與 GET 會得到的相同：自訂的 `key_builder` 被呼叫時，請求的方法會是 `GET`。`vary`、憑證、`private`、`no_store` 與依賴項的標頭都與 GET 相同。

未命中時 handler 會執行，它的回應得到與 GET 相同的 `Cache-Control` 與 `Vary` 處理，`ETag` 與 `Content-Length` 則依它實際產生的內容計算：對 HEAD 省略本文的 handler，送出的是空本文的值。這個回應不會儲存，否則之後的 GET 會拿到空的本文。伺服器會丟棄每個 HEAD 回應的本文。`invalidate()` 建立的是 GET 的快取鍵，因此也會清除 HEAD 讀取的項目。

0.4.2 之前，已接受 HEAD 的路由每次收到 HEAD 請求都會執行 handler，也不會加上這些標頭；現在命中時不會執行 handler。

### 同時發生的未命中 {#concurrent-misses}

預設情況下，每個未命中的請求都會執行 handler：20 個同時送到冷鍵的請求，或在項目剛過期時抵達的請求，會執行 20 次。`coalesce=True` 讓每個行程對每個快取鍵只執行一次：

```python
@app.get("/report")
@cache(ttl=60, coalesce=True)
async def report() -> dict[str, int]: ...
```

第一個未命中的請求照常產生回應。在它執行期間未命中同一個快取鍵的請求，會等它回應後再讀取一次後端，並以儲存的項目回應，`ETag`、`Age` 與 304 的處理與一般命中相同（#252）。若第一個回應沒有儲存（它設定了 cookie、是 `private` 或 `no-store`、狀態碼是錯誤或由依賴項設定、是串流回應，或 handler 拋出例外），每個等待中的請求會自己執行 handler，而且是同時執行，不會一個接一個排隊。

- 只會合併同一個行程內的請求，不會在後端加鎖：每個 worker 對每個冷鍵仍會執行一次 handler。若要讓所有 worker 合計只執行一次，請以 `get_or_set()` 快取成本高的部分，它的 [cache stampede 保護](APP_CACHE.md#stampede-protection)使用分散式鎖。
- 等待中的請求會等到第一個請求的 handler 結束，沒有另外的逾時。讀取後端失敗的請求不會等待。
- HEAD 請求會等待同一個快取鍵正在執行的 GET，但永遠不會讓其他請求等它，因為它的回應不會儲存。
- 它需要正的 `ttl`。搭配永遠不會以儲存的項目回應的 `private=True` 或 `no_cache=True` 時，裝飾器會拋出 `CacheXError`；搭配 `no_store=True` 時則會被忽略，並發出一般的警告。帶有憑證而繞過後端的請求不會被合併。

### 分辨命中與未命中 {#telling-a-hit-from-a-miss}

回應本身看不出 handler 是否執行過，除非在 handler 裡加計數器。`debug_header=True` 會在每個 GET 或 HEAD 回應送出 `X-Cache`：

```python
@app.get("/items")
@cache(ttl=60, debug_header=True)
async def items() -> list[str]: ...
```

| `X-Cache` | 意義 |
|-----------|------|
| `HIT`     | 以儲存的項目回應：沒有執行 handler 的 200，或對應該項目 ETag 的 304。 |
| `MISS`    | 查過後端，且 handler 執行了：冷鍵或已過期的鍵、`no_cache=True`，或回應沒有儲存（設定了 cookie、錯誤狀態碼、串流本文）。 |
| `BYPASS`  | 既沒有讀取也沒有寫入後端：`no_store=True`、`private=True`、沒有正的 `ttl`，或帶有憑證的請求打到沒有 `public` 或 `cache_authorized` 的路由。 |

預設關閉，因為它會向用戶端透露伺服器快取了什麼；請在開發時或內部路由上開啟。這個標頭不會隨項目儲存，並會取代 handler 自己設定的 `X-Cache`。其他方法不會得到這個標頭，就像它們也不會得到 `Cache-Control`。

### 帶有憑證的請求 {#requests-with-credentials}

每個請求都送出 `Authorization` 的單頁應用程式，或每位訪客都有 Session 的網站，在只加上 `@cache` 的路由上完全不會命中快取：每個請求都會繞過後端（見上文）。請依 handler 回傳的內容選擇：

- **每位使用者得到相同的回應**（商品列表、公開文章）：設定 `public=True`。帶有 `Authorization` 或 Session 的請求就會像其他請求一樣讀寫後端。注意 `public=True` 也會把送往下游的標頭改為 `Cache-Control: public, ...`，告訴 CDN 或反向 proxy 即使請求帶有憑證也可以儲存這個回應。只有在這確實成立時才使用它。
- **回應依使用者而不同**（個人資料、購物車、儀表板）：設定 `cache_authorized=True`，並搭配把已驗證的呼叫者身分放進鍵的 `key_builder`，讓每位使用者擁有自己的項目（見[需驗證身分的端點](#authenticated-endpoints)）。鍵中沒有身分時，一位使用者的回應會提供給下一位。回應仍以 `private` 送出，因此下游只有使用者自己的瀏覽器會保留一份。若不需要伺服器端快取，兩個選項都不要設定（或使用 `private=True`），只讓瀏覽器快取它。

為了不讓 0% 的命中率無人察覺，路由第一次因憑證而繞過後端時，會在 `fastapi_cachex.cache` logger 上以 `WARNING` 等級記錄一次，內容包含路由樣板（例如 `'/items/{item_id}'`）、造成繞過的憑證（`Authorization` 標頭、Session 權杖，或不是空的 `request.session` 資料），以及上述兩個選項：

```text
@cache bypassed the shared backend for route '/products': the request carried an Authorization header, so the response is not cached and is sent with Cache-Control: private. If the response is the same for every user, set @cache(public=True) (this also sends Cache-Control: public, so shared caches downstream may store it). If it is per user, set cache_authorized=True with a key_builder that puts the verified caller's identity into the key. Logged once per route and credential; each bypass is logged at DEBUG.
```

這個警告在行程的生命週期內，每個路由與憑證種類只記錄一次（因此每個 worker 一次），而且絕不包含標頭值或權杖。每次繞過仍會以 `DEBUG` 等級記錄。若繞過正是你要的，請在路由上設定 `private=True`，它會繞過後端而不發出警告；或是提高 logger 的等級：

```python
import logging

logging.getLogger("fastapi_cachex.cache").setLevel(logging.ERROR)
```

這也會隱藏下文所述的後端錯誤警告，因此適用時請優先使用 `private=True`。

### 後端發生錯誤時 {#when-the-backend-fails}

`@cache` 採取 fail open。讀取時後端拋出錯誤（例如 Redis 或 Memcached 無法連線），該請求會被當成快取未命中，照常執行 handler。儲存回應時拋出錯誤（例如回應超過 Memcached 的項目大小上限，預設為 1 MB），回應會照常送出，只是不會被儲存。兩種情況都會在 `fastapi_cachex.cache` logger 記錄一則警告，因此後端中斷不會讓有快取的路由變成 500；負載會轉到你的 handler 上，請留意這些警告。

fail open 的速度取決於後端多快回報錯誤。redis-py 8 預設會以指數退避重試失敗的 Redis 指令 10 次，因此在 Redis 拒絕連線時，每次讀取與寫入都要約 3 到 4 秒才會失敗，而同時進行兩者的快取請求約需 7 秒。若 Redis 主機完全沒有回應，每次嘗試還得等到連線逾時，一個請求可能需要約 30 秒。若要在逾時設定內就失敗，可透過後端的關鍵字引數關閉重試並縮短逾時：

```python
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from fastapi_cachex import BackendProxy
from fastapi_cachex.backends import AsyncRedisCacheBackend

backend = AsyncRedisCacheBackend(
    host="127.0.0.1",
    port=6379,
    retry=Retry(NoBackoff(), 0),  # 不重試：第一次錯誤就是最終結果
    socket_connect_timeout=0.25,  # 建立連線的秒數上限
    socket_timeout=0.5,  # 等待回覆的秒數上限
)
BackendProxy.set(backend)
```

取捨請見[Redis 停止運作時快速失敗](BACKENDS.md#failing-fast-when-redis-is-down)。

警告會列出請求的 method、路徑與 `key_ref`（快取鍵的簡短 SHA-256 摘要），但不會列出快取鍵本身：快取鍵含有原始查詢字串、`vary` 標頭值以及任何 `build_cache_key` 元件，可能是 token 或個人資料。完整的快取鍵會以 `DEBUG` 等級連同相同的 `key_ref` 記錄，因此排查問題時在 `fastapi_cachex.cache` 開啟 `DEBUG`，即可將警告對應到其快取鍵。

傳入 `fail_open=False` 則會讓後端錯誤直接往外拋出，使該請求失敗：

```python
@app.get("/report")
@cache(ttl=300, fail_open=False)
async def report():
    return await build_report()
```

這只適用於 `@cache`。`invalidate()`、`CacheManager`、`CacheLock`，以及已棄用的 `StateManager` 與 Session 仍會把後端錯誤拋給呼叫端。

## 快取鍵 {#cache-keys}

快取鍵以下列格式產生，以避免衝突：

```
http:v2|{method}|{host}|{path}|{query_params}
```

`http:v2` 是格式標籤（`CacheKey.FORMAT_TAG`）。之後的鍵格式會使用另一個標籤，因此其鍵不會與這些鍵衝突；在 Redis 與記憶體後端上，`clear_pattern("http:v2|*")` 會移除這個格式的所有 HTTP 快取項目。

這可確保：

- 方法是快取鍵的一部分
- 不同的主機不共用快取（適用於多租戶情境）
- 不同的查詢參數各有獨立的快取項目
- 同一個端點搭配不同參數時可以各自快取

查詢參數會先依名稱排序，再建立快取鍵，因此 `?a=1&b=2` 與 `?b=2&a=1` 共用同一筆項目。排序是穩定的：同名參數的多個值保留用戶端送出的順序，因為以 `tag: list[str]` 讀取的 handler 看到的正是這個順序，所以 `?tag=b&tag=a` 與 `?tag=a&tag=b` 仍是兩筆項目。名稱以解碼後的值比較（`%61` 視為 `a` 排序，快取鍵本來就這樣寫它），每個名稱與值的編碼都與未排序的快取鍵完全相同，只有順序改變：已經依序排列的查詢，不論是否排序都得到相同的鍵。快取鍵原本就視為相同的仍然相同（`?a` 與 `?a=`、`&&` 產生的空段），其餘一律不會合併。0.4.0 起預設會排序（[#72](https://github.com/allen0099/FastAPI-CacheX/issues/72)，見[遷移至 0.4.0](MIGRATING_0_4.md#cache-keys)）。

回應取決於用戶端送出之查詢順序的 handler（例如從 `request.url` 複製的自身連結或分頁連結）應關閉排序，否則第一位呼叫者的順序會被快取，並提供給送出其他順序的呼叫者：

```python
@app.get("/search")
@cache(ttl=60, sort_query=False)
async def search(request: Request, q: str, limit: int = 10):
    return {"self": str(request.url), "items": await run_search(q, limit)}
```

對這樣的路由呼叫 `invalidate()` 時也要傳入 `sort_query=False`（見[使單一快取路由失效](#invalidating-a-single-cached-route)）。

`sort_query` 只套用於預設的 key builder。`build_cache_key()` 也會排序，因此呼叫它的自訂 `key_builder` 不需指定就會排序；在 `@cache` 中同時傳入 `sort_query` 與自訂的 `key_builder`，套用裝飾器時就會拋出 `CacheXError`。若要保留送出的順序，請在 builder 中呼叫 `build_cache_key(request, ..., sort_query=False)`。

在鍵中編碼後超過 200 位元組的查詢字串，會改以 `sha256:` 加上 64 個十六進位字元的摘要儲存，因此用戶端無法讓鍵中查詢的部分無限變長。（超過 250 位元組的整個鍵，含前綴，Memcached 仍會整個雜湊；略低於門檻的查詢配上較長的 host 或路徑仍可能超過。）摘要在排序之後計算，因此順序不同的長查詢仍共用同一筆項目。路徑維持可讀，所以 `clear_path()` 仍找得到該項目（需帶上 `include_params=True`，因為查詢不是空的），監控路由則以 `query_params` 顯示這個摘要。用戶端送出的查詢不可能看起來像摘要：鍵中的 `:` 會寫成 `%3A`。

host 與路徑來自用戶端，因此其中的 `|` 與 `%` 會以百分比編碼寫入（`%7C` 與 `%25`）。含有 `|` 的 `Host` 標頭或路徑因此無法讓各段錯位，使某個請求的快取鍵與另一個請求相同。查詢字串本來就經過 URL 編碼。`clear_path()` 接受應用程式看到的路徑（`request.url.path`），並以同樣方式編碼；`clear_pattern()` 比對的是儲存的快取鍵，所以在模式中要把 `|` 寫成 `%7C`。0.4.0 完全不讀取 0.3.x 寫入的項目，它們會重新快取一次（見[遷移至 0.4.0](MIGRATING_0_4.md#cache-keys)）。

host 會先經過正規化，讓同一個來源的各種寫法共用同一筆項目：轉為小寫（主機名稱不分大小寫），並去除空的連接埠或該 scheme 的預設連接埠（http 為 `:80`，https 為 `:443`）。在 http 上，`Example.com`、`example.com:80` 與 `example.com` 是同一個鍵 `example.com`；`example.com:8080` 保留連接埠，IPv6 位址則保留方括號（`[::1]:8000`）。scheme 以應用程式看到的為準：在終止 TLS 的代理之後，除非套用了代理的標頭（例如 `uvicorn --proxy-headers`），否則 scheme 是 `http`，因此這類代理送來的 `Host: example.com:443` 會保留連接埠。沒有 `Host` 標頭的請求使用 `unknown`。

除此之外，host 仍是用戶端送來的任何值。除非應用程式前方的反向代理或負載平衡器已會拒絕未知的 host，否則請加上 Starlette 的 `TrustedHostMiddleware`，讓偽造的 `Host` 得到 `400`，而不是在快取中塞滿沒有其他人會請求的項目：

```python
from starlette.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["example.com", "*.example.com"]
)
```

### 在鍵中加入其他段 {#adding-components-to-the-key}

需要多一個維度（使用者 ID、租戶、語系）的自訂 `key_builder`，應呼叫 `build_cache_key(request, *components)`，而不是自行重組格式。不傳入任何段時，它回傳的正是預設的鍵；每個段會附加在查詢字串之後：

```
http:v2|{method}|{host}|{path}|{query_params}|{component}|...
```

```python
from fastapi import Request

from fastapi_cachex import build_cache_key


def per_tenant_key(request: Request) -> str:
    return build_cache_key(request, request.state.tenant_id)
```

段必須是 `str` 或 `int`（`int` 以十進位寫入，因此 `1` 與 `"1"` 是同一個段）；其他型別，包括 `None`，都會引發 `TypeError`，避免缺少的 ID 悄悄讓所有這類呼叫者共用同一個 `"None"` 鍵。每個段都與 host 和路徑一樣以百分比編碼，因此含有 `|` 的值無法讓各段錯位。空字串仍是一個段：`build_cache_key(request, "")` 不等於預設的鍵。

由於路徑仍在原本的位置，`clear_path()` 依然找得到這些鍵：不帶 `include_params` 時，會清除該路徑下查詢字串為空的所有項目，不論其他段為何；帶上它則清除該路徑的所有項目。監控路由會把其他段解碼後列在 `extra_components` 中。`default_key_builder(request)` 就是 `build_cache_key(request)`。

`CacheKey` 則是以值的形式表示同一個鍵。`CacheKey.from_request(request, *components)` 建立它，`to_str()` 得到與 `build_cache_key` 相同的字串，`CacheKey.parse(key)` 則把已儲存的鍵解碼為 `method`、`host`、`path`、`query` 與 `extra`；若不是 HTTP 鍵（例如 `CacheManager` 的鍵，或沒有 `http:v2` 標籤的鍵），則回傳 `None`：

```python
from fastapi_cachex import CacheKey

for key in await backend.get_all_keys():
    parsed = CacheKey.parse(key)
    if parsed is not None and parsed.path.startswith("/reports/"):
        print(parsed.host, parsed.query, parsed.extra)
```

Redis 與 Memcached 後端還會在每個鍵前面加上自己的前綴（預設為 `fastapi_cachex:`），讓其他應用程式可以共用同一台伺服器；`MemoryBackend` 沒有前綴。`CacheManager`（見[應用層快取](APP_CACHE.md)）則使用另一個較簡單、以 `cache:` 為前綴的鍵命名空間，而不是這種以 `|` 分隔的格式，因為它的鍵與 HTTP 請求無關。

回傳自行組成、而非由 `build_cache_key()`（或 `CacheKey`）建立之鍵的 `key_builder`，仍可以快取、以 `invalidate()` 使項目失效，也能以 `clear_pattern()` 清除。但這種鍵沒有 `http:v2` 標籤，因此 `clear_path()` 找不到它，監控路由也會略過它。

### 依請求標頭區分 {#varying-on-request-headers}

快取鍵不包含任何請求標頭，因此回應內容取決於 `Accept-Language` 等標頭的路由，會把第一個快取下來的語言提供給所有人。請把這類標頭列在 `vary` 中：

```python
@app.get("/greeting")
@cache(ttl=300, vary=["Accept-Language"])
async def greeting(request: Request):
    return {"text": translate("hello", request.headers.get("accept-language"))}
```

每個列出的標頭都會在鍵中加入一個 `name=value` 段：名稱轉為小寫，值去除前後空白（重複的標頭行以 `,` 串接），缺少的標頭視同空值。這些段與鍵的其他部分一樣經過編碼，並接在 `key_builder` 回傳的鍵之後，因此 `vary` 可以與自訂的 key builder 一起使用：`key_builder` 回傳 `build_cache_key(request, "tenant-1")` 時，鍵為 `http:v2|GET|example.com|/greeting||tenant-1|accept-language=de`。沒有設定 `vary` 的路由，鍵維持不變。

這些名稱也會加入該路由對 GET 或 HEAD 請求的每個回應的 `Vary` 標頭，不論是 200 或 304，也不論是否由後端提供（`private`、`no_store`、繞過後端的 `Authorization` 請求，或未儲存的回應），讓應用程式前方的共用快取也依它們區分。回應已列出的名稱（不分大小寫）不會重複加入，帶有 `Vary: *` 的回應則維持原樣。

`vary` 必須是由標頭欄位名稱組成的 list（或 tuple）。套用裝飾器時，會拒絕 `vary="Accept"` 這類單一字串，以及空名稱、`*` 與任何不是有效欄位名稱的值。

#### 憑證標頭會雜湊 {#credential-headers-are-hashed}

快取鍵並非機密：`get_all_keys()` 會列出它、`/cached-records` 與 `/cached-hits` 監控路由會顯示它，Redis 或 Memcached 的鍵空間也會原樣儲存它。因此對於攜帶憑證的標頭，也就是 `Authorization`、`Proxy-Authorization`、`Cookie` 與 `X-Session-Token`（已棄用的 Session 子系統預設的 `header_name`），不分大小寫，該段存放的是值（依上述方式去除空白並串接）的完整十六進位 SHA-256，而不是值本身：

```
http:v2|GET|example.com|/me||authorization=sha256:3f0a…（64 個十六進位字元）
```

同一個權杖永遠得到同一個摘要，因此會命中自己的項目；兩個不同的權杖則得到兩筆項目。缺少或空白的憑證標頭不會雜湊，而是與其他空標頭一樣維持 `authorization=`，讓所有匿名呼叫者共用一筆項目，鍵也仍看得出這是匿名的那一筆。其他標頭（包括以其他名稱設定的 Session 標頭）都維持可讀；若你的標頭帶有機密，請透過 `key_builder`（自行雜湊）而不是 `vary` 以它作為鍵。

`vary=["Authorization"]` 不會解除針對已授權請求的規則（見[需驗證身分的端點](#authenticated-endpoints)）：除非路由設定 `public=True` 或傳入 `cache_authorized=True`，帶有 `Authorization` 標頭（或 Session）的請求仍會繞過後端。兩者都沒有設定時，只會儲存匿名的 `authorization=` 那一筆。

#### `vary=["Cookie"]` 會發出警告 {#varycookie-warns}

列出 `Cookie` 會以整個 `Cookie` 標頭作為鍵，因此每位帶有不同 Cookie 組合（Session ID、分析用 ID、同意旗標）的訪客都會有自己的項目，而且任何 Cookie 改變時又會多出一筆：項目數量隨訪客人數增長。套用裝飾器時，`@cache` 會發出指向你 `@cache(...)` 那一行的 `UserWarning`。通常你真正需要的是下列其中之一：

- 讓 `key_builder` 回傳 `build_cache_key(request, <真正重要的那個 Cookie 或使用者 ID>)`，並自行在回應設定 `Vary: Cookie`；
- `private=True`，把每位訪客各自的回應交給瀏覽器快取。

針對單一呼叫者的規則仍然適用：帶有 Cookie 的請求會被快取（只有 `Authorization` 或 Session 會觸發繞過），但設定 Cookie 的回應一律不會儲存，並以 `private` 送出，因此每個請求都會更新 Session Cookie 的路由什麼也不會存。若你確實需要 `vary=["Cookie"]`，請在匯入定義該路由的模組之前，用標準的過濾器關閉這個警告：

```python
import warnings

warnings.filterwarnings("ignore", message="cache vary on Cookie")
```

`vary` 中的 `Authorization` 與 `X-Session-Token` 不會發出警告：它們同樣是每位呼叫者一筆項目，但這正是 `vary` 搭配 `cache_authorized=True` 的用途，而且呼叫者在多個請求間會沿用同一個權杖，不像任意組合的 Cookie。

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
> 2. **包含呼叫者身分的 key builder**：確實需要依使用者區分的伺服器端快取時使用。不要設定 `private`：`private=True` 會繞過後端，key builder 就永遠不會被使用。呼叫者以 `Authorization` 標頭或 Session 驗證身分時，請傳入 `cache_authorized=True`：沒有它，這類請求同樣會繞過後端。
>
> 若呼叫者以沒有任何 Session 中介軟體載入的 Cookie 驗證身分（例如由你自己的依賴項讀取的權杖 Cookie），沒有任何條件會觸發繞過：只加上 `@cache` 會把第一位呼叫者的回應提供給所有人，請改用上面兩種做法之一。

```python
from fastapi import Request

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
async def my_dashboard(user: CurrentUser):
    # 對帶有 `Authorization` 或 Session 的請求，以
    # `Cache-Control: private, max-age=60` 送出：只有這個後端與
    # 使用者的瀏覽器會保留一份。
    return build_dashboard(user)
```

依使用者區分的項目只存在於你的後端。應用程式前方的共用快取（CDN、反向代理）只看得到 URL，因此即使設定了 `cache_authorized`，對帶有 `Authorization` 或 Session 的請求的每個回應仍帶有 `private`。若身分改由你自己的 Cookie 提供，沒有任何條件會把請求標記為帶有憑證，裝飾器的標頭會原樣送出：請設定 `private=True`（做法 1），或在共用快取可能儲存它時自行送出 `Vary: Cookie`。

> [!CAUTION]
> key builder 決定了誰能看到誰的資料，因此它讀取的身分必須來自已經驗證過的來源：已檢查權杖中的 claim、你的依賴項解析出的使用者，或驗證中介軟體寫入 `request.state` 的值。
>
> ```python
> # ❌ 絕對不要這樣做：任何人都能送出這個標頭。
> user_id = request.headers.get("x-user-id", "anonymous")
> ```
>
> 以原始請求標頭組成的鍵等同於水平權限提升：送出 `X-User-Id: <someone-else>` 就會拿到該使用者的快取回應。

key builder 只在 `@cache` 讀取或寫入後端時執行，因此 `no_store=True`、`private=True`、沒有 `ttl` 的路由，以及路由未設定 `public=True` 或 `cache_authorized=True` 時帶有 `Authorization` 或 Session 的請求，都不會呼叫它。0.3.8 之前它仍會被呼叫，但只用於除錯日誌。請讓它不帶副作用。

key builder 必須是回傳 `str` 的同步函式，呼叫時不會被 await。`async def` 函式、具有 `async def __call__` 的物件，或包裝上述兩者的 `functools.partial`，都會在套用 `@cache` 時以 `CacheXError` 拒絕；`invalidate()` 也會在存取後端之前拒絕它們。仍然回傳非 `str` 的 builder（例如回傳協程的同步包裝函式）會在請求時拋出 `CacheXError`。`fail_open` 不涵蓋這種情況：這是路由的錯誤，不是後端故障。需要非同步讀取的資料（例如從資料庫取得使用者），請在依賴項或中介軟體中讀取，放到 `request.state` 供 builder 使用。

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

    # 依模式清除：比對整個鍵 http:v2|method|host|path|query
    await cache.clear_pattern("http:v2|GET|*|/api/users/*")
    # 你自己組成的鍵（例如 CacheManager 的鍵）可以直接比對
    await cache.clear_pattern("cache:user:*")

    # 清除全部
    await cache.clear()  # 移除所有快取項目
```

`clear_path()` 會比對該路徑在所有方法與主機下的項目。只寫成路徑的模式（例如 `clear_pattern("/api/users/*")`）無法比對到 HTTP 鍵；這類呼叫沒有清除任何項目時，會發出 `RuntimeWarning`，提示你改用 `clear_path()`。

Memcached 無法列舉鍵，因此 `clear_path()` 完全找不到 HTTP 項目：它只會刪除名稱與路徑完全相同的鍵，而且每次呼叫都會發出 `RuntimeWarning`。在 Memcached 上請改用下方的 `invalidate()`。

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

`invalidate(request, key_builder=None, vary=None, *, sort_query=None)` 在項目存在且已移除時回傳 `True`，否則回傳 `False`，包括尚未設定後端的情況。後端本身的錯誤則會拋給呼叫端（見[後端發生錯誤時](#when-the-backend-fails)）。傳入的請求必須能產生快取路由的鍵：相同的方法、主機、路徑與查詢字串。如果快取路由使用自訂的 `key_builder`、`vary` 或 `sort_query`，這裡也要傳入相同的值，否則鍵不會相符：`invalidate()` 無法從路由讀取這些設定。使用 `vary` 時，只會刪除請求本身的標頭值所選中的變體，`clear_path()` 則會移除所有變體。預設情況下，請求的查詢會以 `@cache` 相同的方式排序，因此 `?b=2&a=1` 會刪除為 `?a=1&b=2` 儲存的項目；對設定了 `sort_query=False` 的路由，這裡也要傳入 `sort_query=False`。

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
    dependencies=[Depends(verify_admin)],  # 必填，只能以關鍵字傳入
    include_content_preview=True,  # 預設 False：不含本文預覽
)
```

- `GET {prefix}/cached-hits`：列出每筆快取項目，拆分為方法、主機、路徑與查詢（超過 200 位元組的查詢為 `sha256:<十六進位>`），附上 ETag 與到期時間，另外統計有效與已過期的項目數，以及不重複的快取路徑。它不會計算命中次數。
- `GET {prefix}/cached-records`：列出每筆快取紀錄的大小、到期時間、`media_type`（儲存的回應的媒體類型，沒有時為 `null`），以及在 `include_content_preview=True` 時快取內容前 100 個位元組的預覽。預設 `content_preview` 為 `null`，不會有任何回應本文離開伺服器；鍵、大小與到期時間仍會回報。`content_type` 一律是 `"bytes"`，只為相容而保留；請改讀 `media_type`。

兩個路由都只列出路由項目（格式為 `http:v2|method|host|path|query` 的鍵）；`CacheManager`、Session、state 與鎖的鍵都會略過，未使用 `build_cache_key()` 的 `key_builder` 產生的鍵也一樣。

> [!WARNING]
> **這些路由本身沒有任何身分驗證。** `include_in_schema=False` 只是讓它們不出現在 OpenAPI 文件中；任何猜到路徑的人都能讀取。它們會暴露整個路由結構（包含查詢字串），設定 `include_content_preview=True` 時還會暴露每個快取回應的開頭。因此 `dependencies` 為必填：請傳入 `dependencies=[Depends(your_auth)]`，或將路由掛載在僅供內部使用的應用程式上。若本機或測試用的應用程式確實要保持開放，請傳入 `dependencies=[]` 明確選擇不設防護。

[`examples/http_cache.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/http_cache.py) 以環境變數中的權杖保護它們，未設定該變數時路由一律拒絕存取（程式碼註解為英文）：

<!-- fmt:off -->
```python
--8<-- "examples/http_cache.py:admin"
```
<!-- fmt:on -->

> [!NOTE]
> Memcached 無法列舉鍵，因此在 Memcached 上這兩個路由都不會回傳任何內容。
