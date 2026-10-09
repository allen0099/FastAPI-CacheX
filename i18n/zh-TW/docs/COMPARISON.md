# 何時使用 FastAPI-CacheX {#when-to-use-fastapi-cachex}

FastAPI-CacheX 在 FastAPI 應用程式內快取 HTTP 回應，並正確處理相關的 HTTP 語意：`Cache-Control`、`ETag` 與 `If-None-Match`、`Vary`，以及帶有憑證的請求。它也在同樣的後端上提供一個小型的應用層快取和分散式鎖。它不是每一種快取需求的最佳工具。本頁說明它擅長什麼、其他常見選擇擅長什麼，以及什麼時候其他選擇更合適。

其他函式庫的說明以它們在 2026 年 10 月的最新版本為準。如果內容已經過時，歡迎[開 issue](https://github.com/allen0099/FastAPI-CacheX/issues)。

## 一覽 {#at-a-glance}

其他函式庫的資訊取自最後一列所列版本的原始碼。標示 0.4.2 的功能尚未包含在 FastAPI-CacheX 的任何發行版中。

| | FastAPI-CacheX | fastapi-cache2 | cashews | aiocache |
|---|---|---|---|---|
| 快取的對象 | FastAPI 的 GET 回應；JSON 值；函式結果（`@cached`，0.4.2） | 端點與一般函式的回傳值 | 任何非同步函式的回傳值 | 任何非同步函式的回傳值；鍵值 API |
| 快取鍵的來源 | 請求的方法、主機、路徑與查詢字串 | 函式的模組、名稱與引數 | 函式名稱與引數，或樣板 | 函式名稱與引數，或 key builder |
| `Cache-Control`、`ETag`、`304` | 依裝飾器的參數產生；`If-None-Match` 相符時回 `304` | 端點接收 `Response` 時，送出 `max-age`、弱 `ETag` 並回 `304` | 透過它的 `CacheRequestControlMiddleware` 與 `CacheEtagMiddleware` | 無 |
| 用戶端請求的 `Cache-Control` 標頭 | 忽略，用戶端無法略過快取 | 遵循 `no-store` 與 `no-cache` | 由中介軟體遵循（`no-cache`、`no-store`、`max-age`） | — |
| 帶 `Authorization` 或 Session 的請求 | 略過共用快取，除非明確選擇共用 | 和其他請求一樣被快取 | 取決於你的快取鍵和中介軟體設定 | — |
| 序列化 | JSON，不用 pickle | 預設 JSON，可選 pickle | 預設 pickle，可選 HMAC 簽章；可選 JSON | pickle、JSON、msgpack、字串或不序列化 |
| 後端 | 記憶體、Redis、Memcached | 記憶體、Redis、Memcached、DynamoDB | 記憶體、Redis（含 cluster 與 client-side caching）、diskcache | 記憶體、Redis、Memcached |
| Stampede 防護 | `get_or_set()` 搭配分散式鎖；`@cache` 的 `coalesce=True` 在單一行程內合併（0.4.2） | 無 | `locked`、`early`、`soft`、`thunder_protection` | `cached_stampede`（以鎖實作） |
| 標籤、提前更新、指標 | 無 | 無 | 標籤、early 與 soft 提前更新、Prometheus 中介軟體、回呼 | 命中率與計時外掛 |
| 其他功能 | `CacheLock`、原子計數器、監控路由 | — | 速率限制、斷路器、Bloom filter、鎖 | `RedLock`、`OptimisticLock`、`multi_cached` |
| 最新版本 | 0.4.1（2026-10-03）；Python 3.10+、FastAPI 0.133+、Starlette 1.0+ | 0.2.2（2024-07-24）；Python 3.8+ | 7.6.0（2026-09-17）；Python 3.10+ | 0.12.3（2024-09-25） |

## FastAPI-CacheX {#fastapi-cachex}

當你要快取的是 **FastAPI 回應**，而且希望快取的行為和 HTTP 快取一致時，選擇它：

- **HTTP 語意。** `@cache` 依參數產生 `Cache-Control`，加上弱 `ETag`，並在 `If-None-Match` 相符時回 `304`，快取未命中和 `no_cache` 路由也一樣。命中時送出 `Age`，遵循 `Vary`（`vary=` 把請求標頭加進快取鍵），並以快取的 `GET` 回應 `HEAD`（0.4.2）。見 [HTTP 快取](HTTP_CACHING.md)。
- **預設對帶憑證的請求安全。** 預設的快取鍵不含使用者身分，所以帶 `Authorization` 或 Session 的請求會略過共用快取，並以 `Cache-Control: private` 回應；設定 cookie 的回應永遠不會存入快取。要共用時設定 `public=True`；要每個使用者各自一筆項目時，設定 `cache_authorized=True` 並搭配把使用者放進快取鍵的 key builder（見[帶有憑證的請求](HTTP_CACHING.md#requests-with-credentials)）。
- **快取鍵來自請求。** 快取鍵是方法、主機、路徑與排序後的查詢字串，和下游 HTTP 快取看到的一致，也可以用 `invalidate(request)` 或 `clear_path()` 讓路由的快取失效。
- **不用 pickle。** 回應與 `CacheManager` 的值都以 JSON 儲存，從共用的 Redis 讀出的項目在反序列化時不會執行程式碼。
- **失敗時放行（fail open）。** 後端無法使用時，`@cache` 記錄日誌並執行 handler，快取故障不會變成 API 故障。
- **同一個後端上的小工具。** `CacheManager`／`AppCache` 的 `get_or_set()` 以分散式鎖保護，`@cached` 快取一般函式（0.4.2），`CacheLock` 讓多個 worker 之間同時只有一個持有者。

它不做、或只簡單做到的事：

- 只快取 `GET`（以及 `HEAD`）回應，不快取 `POST`。
- 函式快取很基本：沒有提前或機率式更新、沒有標籤、不會在一個呼叫者更新時提供過期值、沒有指標掛鉤。
- 三種後端：記憶體、Redis 與 Memcached。Memcached 無法列舉鍵，所以依模式或路徑清除在它上面不會有任何作用。
- 伺服器端快取永遠不提供過期內容；`stale-while-revalidate` 和 `stale-if-error` 只寫進標頭給下游快取使用。

## fastapi-cache2 {#fastapi-cache2}

[fastapi-cache2](https://github.com/long2ice/fastapi-cache) 是安裝數最多的 FastAPI 快取。`@cache(expire=60)` 快取端點或任何函式的回傳值，快取鍵取自函式的模組、名稱與引數。

以下情況選擇它：

- 你已經在用，而且運作良好。它的 API 小且廣為人知。
- 你想用同一個裝飾器處理端點和輔助函式，並以引數作為快取鍵。
- 你需要 DynamoDB，或需要支援 Python 3.8 或 3.9。

比較時要注意：

- 快取鍵不包含 URL。兩個得到相同引數的請求共用同一筆項目；如果回應取決於引數以外的東西，例如 handler 透過 `Request` 讀取的標頭，就需要自訂 `key_builder`。
- 帶 `Authorization` 或 cookie 的請求和其他請求一樣被快取。每個使用者各自不同的端點必須自行把使用者放進快取鍵。
- 它的最新版本發行於 2024 年 7 月。

要轉換過來，請見[從 fastapi-cache2 遷移](MIGRATING_FROM_FASTAPI_CACHE2.md)。

## cashews {#cashews}

[cashews](https://github.com/Krukov/cashews) 是非同步 Python 的通用快取工具組，是本頁幾個函式庫中功能最多的。

當你主要快取的是**函式結果**而不是 HTTP 回應，而且需要的不只是 TTL 時，選擇它：

- early 或 soft 提前更新：熱門的鍵在過期前就重新計算，不必讓每個呼叫者都等待快取未命中。
- 標籤：一次讓所有依賴同一筆資料的項目失效。
- 同一套設定中提供速率限制、斷路器、Bloom filter 與鎖。
- Prometheus 指標、Redis Cluster、Redis client-side caching 或磁碟快取。

它的 FastAPI 中介軟體提供 `Cache-Control`、`Age` 與 `ETag` 處理。和這裡的 `@cache` 不同，它們讓用戶端的 `Cache-Control: no-cache` 或 `max-age=0` 略過快取，這在除錯時很方便，但也讓任何用戶端都能把請求直接送到 handler。值預設以 pickle 儲存；如果還有其他人能寫入你的 Redis，請設定 `secret` 讓值經過簽章，或改用 JSON 序列化器。

## aiocache {#aiocache}

[aiocache](https://github.com/aio-libs/aiocache) 是通用的非同步鍵值快取，提供裝飾器（`@cached`、`@cached_stampede`、`@multi_cached`）、可替換的序列化器，以及命中率與計時外掛。它本身沒有 HTTP 或 FastAPI 相關功能。

當你想要一個單純的非同步快取 API，在 FastAPI 之外或搭配任何 web 框架使用，並自行處理 HTTP 標頭時，選擇它。它的最新版本發行於 2024 年 9 月。

## 在應用程式前面放 HTTP 快取或 CDN {#an-http-cache-or-cdn-in-front-of-the-app}

反向 proxy（nginx、Varnish）或 CDN（Cloudflare、Fastly、CloudFront）在請求到達 Python 之前就快取回應。對於**每位訪客都相同的公開回應**，例如首頁、文章或公開的商品列表，這通常是更好的選擇：命中時不佔用任何 worker，快取離用戶端更近，也能吸收原本會打到應用程式伺服器的流量高峰。

以下情況就沒那麼合適：

- 回應取決於請求者是誰。共用快取以 URL（加上 `Vary` 列出的標頭）作為鍵，在那裡把每個使用者的回應分開很容易出錯；而且除非回應明確允許，共用快取不會儲存對帶 `Authorization` 請求的回應。
- 你需要在資料變更的當下，從應用程式碼讓項目失效，而不想呼叫 CDN 的清除（purge）API。
- 你沒有使用 proxy 或 CDN，例如內部 API。

兩者可以並用。`@cache` 會寫出 CDN 讀取的 `Cache-Control` 和 `ETag` 標頭，CDN 負責提供公開路由，`@cache` 則為仍然到達應用程式的請求（邊緣節點尚未快取，或 `no-cache` 重新驗證）保留伺服器端的副本。使用 `public=True` 時要小心：它告訴所有下游快取，即使請求帶有憑證，也可以儲存這個回應。

## 如何選擇 {#which-one-to-choose}

| 你想要…… | 合適的選擇 |
|----------|------------|
| 快取 FastAPI 的 GET 回應，正確處理 `Cache-Control`、`ETag` 與 `304`，並讓每個使用者各自不同的回應不進入共用快取 | FastAPI-CacheX |
| 以最低成本把公開頁面提供給大量訪客 | CDN 或反向 proxy，由 `@cache` 產生它需要的標頭 |
| 快取函式結果，並需要提前更新、標籤、速率限制或指標 | cashews |
| 與框架無關的非同步鍵值快取 | aiocache 或 cashews |
| 保留運作良好的既有 fastapi-cache2 設定 | fastapi-cache2 |
