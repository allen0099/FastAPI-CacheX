# 正式環境檢查清單 {#production-checklist}

FastAPI-CacheX 應用程式承接真實流量之前要確認的事項。每一項都連到說明它的段落；大多只需要一行設定。

## 後端 {#backend}

- [ ] **在啟動時設定後端。** 沒有設定時，`@cache` 會退回每個行程各自的 `MemoryBackend`，並記錄一則 `WARNING`：多個 worker 時每個 worker 有自己的快取，失效操作只會到達其中一個。超過一個行程時請使用 Redis 或 Memcached（[選擇後端](BACKENDS.md#choosing-a-backend)）。
- [ ] **為每個服務與環境設定自己的 `key_prefix`。** 使用預設值時，同一台伺服器上的兩個應用程式會讀到彼此的值、共用鎖名稱與 tag，其中一個的 `clear()` 也會清掉另一個的項目（[選擇後端](BACKENDS.md#choosing-a-backend)）。
- [ ] **讓後端快速失敗。** 預設情況下，Redis 停止運作時一個請求要好幾秒才會放棄。請設定較短的逾時，並在後端設定 `retry=Retry(NoBackoff(), 0)`（`RedisConfig` 則為 `retries=0`）（[Redis 停止運作時快速失敗](BACKENDS.md#failing-fast-when-redis-is-down)）；Memcached 則設定 `connect_timeout` 與 `timeout`（[Memcached](BACKENDS.md#memcached)）。
- [ ] **設定 Redis 連線池大小。** redis-py 8 每個客戶端最多允許 100 條連線，超過時指令會立刻失敗，`@cache` 會把它當成 Redis 停止運作。請使用依一個 worker 同時處理的請求數設定大小的 `BlockingConnectionPool`（[連線池大小](BACKENDS.md#redis-pool-size)）。
- [ ] **在關閉時關閉後端**，寫在應用程式的 lifespan 中（[關閉後端](BACKENDS.md#closing-a-backend)）。

## 誰會看到什麼 {#who-sees-what}

- [ ] **清楚哪些路由是每個使用者各自的。** 帶有 `Authorization`、Session、API key，或路由的依賴項會讀取之 cookie 的請求，會繞過後端。`public=True` 讓所有呼叫者共用同一個項目，因此只在回應對每個人都相同時使用。`cache_authorized=True` 只有在快取鍵能區分呼叫者時才有作用：加入使用者的 `key_builder`，或對 `Authorization`、`X-API-Key` 等憑證標頭設定 `vary`（[帶有憑證的請求](HTTP_CACHING.md#requests-with-credentials)、[需驗證身分的端點](HTTP_CACHING.md#authenticated-endpoints)）。
- [ ] **對回應所依賴的每個標頭設定 `vary`**，例如 `Accept-Language`；沒有設定時，第一個版本會送給所有人（[依請求標頭區分](HTTP_CACHING.md#varying-on-request-headers)）。
- [ ] **不要開啟 `debug_header`。** `X-Cache-Reason` 會讓客戶端知道它的請求帶有 Session，或後端發生錯誤（[分辨命中與未命中](HTTP_CACHING.md#telling-a-hit-from-a-miss)）。
- [ ] **保護監控路由。** `add_routes()` 本身沒有驗證；請傳入會檢查管理者身分的 `dependencies=[Depends(...)]`（[監控路由](HTTP_CACHING.md#monitoring-routes)）。

## 負載與中斷 {#load-and-outages}

- [ ] **合併昂貴路由的未命中。** `coalesce=True` 讓同時發生的未命中在每個 worker 中每個鍵只執行一次 handler，後端中斷時也是如此；`coalesce="distributed"` 則是跨 worker 只執行一次（[同時發生的未命中](HTTP_CACHING.md#concurrent-misses)）。
- [ ] **決定 handler 失敗或變慢時路由要怎麼回應。** `serve_stale` 會繼續以過期的項目回應（[提供過期的回應](HTTP_CACHING.md#serving-stale-responses)）。
- [ ] **選擇 `fail_open`。** 預設情況下，`@cache` 中的後端錯誤會被記錄，請求不經快取照常執行；`fail_open=False` 則讓它成為錯誤（[後端發生錯誤時](HTTP_CACHING.md#when-the-backend-fails)）。

## 寫入與部署 {#writes-and-deploys}

- [ ] **每次寫入後都使快取失效**，使用 `invalidate()`、`invalidate_path()` 或 tag；tag 在 Memcached 上也能運作（[清除快取](HTTP_CACHING.md#clearing-the-cache)）。
- [ ] **處理中斷期間失敗的失效操作**：它會在你的寫入提交之後才拋出錯誤（[寫入時後端停止運作](HTTP_CACHING.md#invalidating-during-an-outage)）。
- [ ] **依資料最多可以多舊來選擇每個 `ttl`。** 它就是一次沒有執行的失效最多讓舊資料留存的時間（使用 `serve_stale` 時為 `ttl + stale_ttl`）。
- [ ] **為改變回應形狀的部署做好準備**：除非前綴、鍵或 tag 改變，舊的項目會一直被送出，直到過期（[選擇後端](BACKENDS.md#choosing-a-backend)）。

## 觀察 {#observing-it}

- [ ] **以 `set_event_handler()` 計算命中、未命中與錯誤**（[計算命中與未命中](HTTP_CACHING.md#counting-hits-and-misses)）。
- [ ] **保留函式庫的警告記錄**：`@cache` 會以 `WARNING` 將後端失敗記錄在 `fastapi_cachex.cache` logger，每一種每分鐘最多一次；`CacheManager`、`CacheLock` 與失效操作則會拋出錯誤（[後端發生錯誤時](HTTP_CACHING.md#when-the-backend-fails)）。
- [ ] **每個測試使用新的快取**，並在需要每個請求都執行 handler 的測試中關閉快取（[測試你的應用程式](TESTING.md)）。
