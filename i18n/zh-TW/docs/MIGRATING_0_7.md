# 遷移至 0.7.0 {#migrating-to-070}

本頁列出 0.7.0 對以 0.6.x 撰寫的程式碼造成的變更。請先升級到 0.6.5（最後一個 0.6.x 版本）並處理它顯示的警告；剩下的部分說明如下。

版本只從 `master` 發佈，所以第一個 0.7.0 變更合併之後，0.6.x 就不再發佈新版本，安全修補也包括在內（見[安全政策](https://github.com/allen0099/FastAPI-CacheX/blob/master/SECURITY.md)）。

## redis-py 7.4 或更新版本 {#redis-74}

0.7.0 的 Redis 後端需要 redis-py 7.4 或更新版本（[#605](https://github.com/allen0099/FastAPI-CacheX/issues/605)）。7.4 之前，若後端的連線池第一次是在某個 event loop 上使用，而那個 event loop 之後關閉了，後端就再也無法恢復：在之後的 event loop 上同時進行的呼叫會拋出 `RuntimeError: ... is bound to a different event loop`，那個 event loop 以及之後的每一個都是如此。每個測試各自在新的 event loop 上執行的應用程式，或多次以 `asyncio.run()` 呼叫後端的應用程式，都會遇到這個情況。從 7.4 起，用戶端會丟棄過時的連線並重新連線。

0.6.5 會先預告：以較舊的 redis-py 建立 Redis 後端時，每個行程會發出一次 `FutureWarning`：

```text
FutureWarning: fastapi-cachex 0.7.0 will require redis-py 7.4 or later; redis-py 5.3.0 is installed. ...
```

解法是升級 redis-py，程式碼不需要修改：

```bash
pip install "redis>=7.4"
```

安裝 `fastapi-cachex[redis]` 0.7.0 時會自動帶入 redis-py 7.4 或更新版本；請不要把它釘在更低的版本。這個下限只透過 extra 生效：自行列出 `redis` 的應用程式（`redis==5.3.0`）仍然裝得上 0.7.0，也不會有任何警告，因此請自行把版本提高到 7.4 或更新。這個警告只存在於 0.6.5，因此下面的過濾規則只在你仍使用 0.6.5 時需要。

在能升級之前，把警告轉為錯誤的測試（`filterwarnings = ["error"]`）可以忽略這一個警告：

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore:fastapi-cachex 0.7.0 will require redis-py 7.4:FutureWarning",
]
```

在 pytest 之外，請在建立第一個後端之前呼叫 `warnings.filterwarnings("ignore", message="fastapi-cachex 0.7.0 will require redis-py 7.4", category=FutureWarning)`，或設定 `PYTHONWARNINGS="ignore:fastapi-cachex 0.7.0 will require redis-py 7.4:FutureWarning"`。

## HTTP 快取鍵包含 scheme {#key-scheme}

`@cache` 為回應建立的鍵不包含 scheme：`http://example.com/page?p=2` 與 `https://example.com/page?p=2` 的鍵都是 `http:v2|GET|example.com|/page|p=2`。為純 http 請求快取的回應，會被送給下一個 https 請求，因此回應內容中以請求 URL 建立的絕對連結（例如分頁的 `next` 連結），會把 `http://` 連結交給 https 用戶端（[#576](https://github.com/allen0099/FastAPI-CacheX/issues/576)）。

0.7.0 會把 scheme 加入鍵中，並把格式標籤改為 `http:v3`，讓 http 與 https 請求使用各自的項目。對升級的影響：

- 0.7.0 不會讀取 0.6.x 寫入的項目：每個路由的快取都會從空的狀態重新開始一次。舊項目會隨各自的 `ttl` 過期；在 Redis 上要立刻移除它們，請在升級後執行 `clear_pattern("http:v2|*")`；但要等你的 key builder 都不再寫入 `http:v2|` 鍵之後（見最後一項），因為它也會刪除那些鍵（Memcached 無法列舉鍵，見[選擇後端](BACKENDS.md#choosing-a-backend)）。
- 滾動部署期間，0.6.x 與 0.7.0 的實例不會讀取彼此的項目，其中一個版本以鍵進行的失效操作（`invalidate()`、`invalidate_path()`、`clear_path()`、`clear_pattern()`）也不會到達另一個版本的項目。tag 則會：任一版本的 `invalidate_tags()` 都會讓兩個版本中帶有該 tag 的項目變成未命中。`CacheManager` 與 `@cached` 的值則由兩個版本共同讀寫。請留意各個 `ttl`、使用 tag，或一次更新所有實例。
- `invalidate()`、`invalidate_path()` 與 `clear_path()` 會清除兩種 scheme 的項目，因此經由 https 進行的寫入，也會移除同一個 URL 的 http 項目。
- 為 `clear_pattern()` 手寫的模式，例如 `"http:v2|GET|*|/api/users/*"`，從 0.7.0 起不會符合任何鍵，也不會報錯。單一路徑請改用會跟著鍵格式變更的 `clear_path()`；它不接受前綴。若要清除前綴，請依新的標籤與 scheme 欄位改寫模式：`clear_pattern(f"{CacheKey.FORMAT_TAG}|*|GET|*|/api/users/*")` 會同時符合兩種 scheme。
- scheme 是 Starlette 回報的值（`request.url.scheme`）。在終止 TLS 的代理之後，每個請求看起來都是 `http`，因此 http 與 https 仍然共用同一個項目，除非伺服器信任代理的 `X-Forwarded-Proto`。uvicorn 預設就會讀取這個標頭，但只接受 `--forwarded-allow-ips` 中的位址（未設定時為 127.0.0.1），因此位於其他主機的代理需要 `--forwarded-allow-ips=<代理的位址>`。
- 呼叫 `build_cache_key()` 的自訂 `key_builder` 會自動跟著改變。自行組出鍵的則維持原樣：它的鍵仍然混用 http 與 https，而它以 `http:v2|` 格式寫入的鍵，`CacheKey.parse()`、`clear_path()` 與監控路由都不再認得。請改用 `build_cache_key()`。
- `CacheKey` 新增 `scheme` 欄位：自行建立 `CacheKey` 或讀取 `CacheKey.parse()` 結果的程式碼需要處理它；從 `/cached-hits` 或 `get_all_keys()` 讀取鍵的程式碼，會看到新的標籤與 scheme。監控路由的紀錄（`CacheHitRecord`、`CachedRecord`）新增了 `scheme` 欄位，自行建立時必須以關鍵字引數提供（0.7.1 起；在 0.7.0，為 0.6.x 寫的位置引數呼叫會讓每個欄位錯位一格）。

## `MemoryBackend.lock` 與 `start_cleanup()` 已移除 {#memory-backend}

兩者都在 0.6.2 棄用，每次使用都會發出 `DeprecationWarning`（[#504](https://github.com/allen0099/FastAPI-CacheX/issues/504)）。

- `MemoryBackend.lock`：在單一 event loop 上，每個操作本來就是原子操作，而這個鎖也不提供執行緒安全。要讓多個操作成為一個步驟，請使用 [`CacheLock`](LOCK.md) 或你自己的 `asyncio.Lock`。
- `MemoryBackend.start_cleanup()`：第一次讀取或寫入就會啟動清理任務，因此請移除這個呼叫。`stop_cleanup()` 仍然保留。
