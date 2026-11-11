# 測試使用 FastAPI-CacheX 的應用程式 {#testing}

快取的用途是把狀態從一個請求帶到下一個請求；稍不留意，它也會把狀態從一個測試帶到下一個測試。有三樣東西在測試結束後仍然存在，除非你重設它們：

- **註冊在 `BackendProxy` 的後端。** 它是整個行程共用的。`@cache` 以及 `CacheBackend`／`AppCache` 依賴項第一次找不到後端時，還會註冊一個行程內的 `MemoryBackend`，而它同樣會一直留著。
- **註冊在 `CacheManagerProxy` 的 `CacheManager`。** `AppCache` 與 `@cached` 會在第一次使用時建立一個並保留下來，連同它所使用的後端。
- **快取項目本身。** `@cache` 路由會繼續用先前測試為相同方法、主機、路徑與查詢所儲存的項目來回應。

症狀是：測試單獨執行會通過，放進整個套件就失敗，或只有在特定順序下才通過。

## 每個測試一份全新的快取 {#fresh-cache}

讓每個測試擁有自己的 `MemoryBackend`，並在前後清除兩個 proxy。在 `conftest.py` 中：

```python
from collections.abc import Iterator

import pytest

from fastapi_cachex import BackendProxy, CacheManagerProxy
from fastapi_cachex.backends import MemoryBackend


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[MemoryBackend]:
    backend = MemoryBackend()
    BackendProxy.set(backend)
    CacheManagerProxy.set(None)
    yield backend
    BackendProxy.set(None)
    CacheManagerProxy.set(None)
    backend.stop_cleanup()
```

`BackendProxy.set(None)` 只會取消註冊後端，不會清空或關閉它。這裡的後端會隨測試一起丟棄。`stop_cleanup()` 會在清理任務的事件迴圈仍開啟時取消該任務，而且與 `aclose()` 不同，它不需要 `await`，所以同步的 fixture 也能呼叫。清除 `CacheManagerProxy` 同樣重要：從先前測試留下來的 manager 仍會寫入那個測試的後端。重設 proxy 不會影響你明確傳入的後端或 manager（`CacheManager(backend=...)`、`@cached(manager=...)`、`CacheLock(backend=...)`）；它們需要自己的 fixture。

在依賴這份隔離之前，先證明它有效。套件的第一個測試應該是「項目一旦外洩就會失敗」的測試：兩個應用程式對同一個請求給出不同的答案。

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import cache


def make_app(answer: str) -> FastAPI:
    app = FastAPI()

    @app.get("/answer")
    @cache(ttl=60, debug_header=True)
    async def read_answer() -> dict[str, str]:
        return {"answer": answer}

    return app


def test_first_run() -> None:
    client = TestClient(make_app("run-a"))

    assert client.get("/answer").json() == {"answer": "run-a"}
    assert client.get("/answer").headers["X-Cache"] == "HIT"


def test_second_run_does_not_see_the_first() -> None:
    response = TestClient(make_app("run-b")).get("/answer")

    assert response.json() == {"answer": "run-b"}
    assert response.headers["X-Cache"] == "MISS"
```

少了這個 fixture，第二個測試會以 `HIT` 拿到 `{"answer": "run-a"}`：兩個應用程式都會建立鍵 `http:v2|GET|testserver|/answer|`，而第一個測試留下的後備後端仍然註冊著。

## 關閉快取 {#switching-off}

開發時，被快取的路由會讓你看不到剛改好的結果；而與快取無關的測試，沒有快取會比較單純。一個開關就能關閉整個行程的快取：

```python
from fastapi_cachex import set_enabled

set_enabled(False)  # 用 set_enabled(True) 重新開啟
```

或者不改程式碼，以 `FASTAPI_CACHEX_DISABLED=1`（也接受 `true`、`yes`、`on`）啟動行程；這個變數只在匯入 `fastapi_cachex` 時讀取一次，並會記錄一筆警告。`is_enabled()` 可以查詢目前的狀態。

關閉期間：

| | 行為 |
|---|---|
| `@cache` | 每個請求都執行 handler，不讀也不寫後端。回應的 `Cache-Control` 與 `ETag` 和開啟快取時相同，符合的 `If-None-Match` 仍會得到 304，所以瀏覽器或前方的 CDN 行為不變。`X-Cache` 為 `BYPASS`，並帶有 `X-Cache-Reason: disabled`。 |
| `@cached`、`get_or_set()` | 每次呼叫都執行函式或 factory：不讀取、不加鎖、不寫入。值仍會經過 JSON 來回轉換（以及 `type_`），所以形狀與命中時相同。每次呼叫的[事件](HTTP_CACHING.md#counting-hits-and-misses)都是 `miss`。 |
| `CacheManager.get()`、`set()`、`add()`、`delete()`、各後端、`CacheLock`、`invalidate()` 與其他清除函式 | 照常運作。 |

最後一列是刻意的。`get()` 與 `set()` 也被用來*儲存*東西：一次性驗證碼、計數器、另一個請求會讀回的旗標。如果開關讓 `get()` 什麼都讀不到，關閉快取就會讓這些功能壞掉，而不只是讓應用程式變慢。代價是：寫成 `get()`、計算、`set()` 的程式碼在開關關閉時仍會回傳已儲存的值；改寫成 `get_or_set()`，它就會跟著開關走。

在測試套件中，對與快取無關的測試關閉它，與快取有關的測試則保持開啟：

```python
@pytest.fixture
def no_cache() -> Iterator[None]:
    set_enabled(False)
    yield
    set_enabled(True)
```

自行建立的 `CacheManager()` 仍然需要先設定後端。不要在正式環境設定這個變數：每個請求都會執行它的 handler。

## 斷言某個回應確實被快取 {#asserting}

`debug_header=True` 會讓 `@cache` 在 `X-Cache` 標頭中說明每個 GET 或 HEAD 請求是如何回應的：`HIT`（由已儲存的項目回應）、`MISS`（handler 執行了，且查詢過後端）或 `BYPASS`（既沒有讀取也沒有寫入後端）。比起在 handler 裡放呼叫計數器，斷言這個標頭更穩定。除非你希望用戶端看得到，否則在正式環境請保持關閉；測試可以透過設定把它打開。

要測試過期，不要用 sleep 等 TTL 過去。改為移除項目，再斷言下一個請求是 `MISS`。後端的方法都是 coroutine，所以同步測試要用 `asyncio.run` 執行：

```python
import asyncio


def test_entry_is_gone_after_clear(fresh_cache: MemoryBackend) -> None:
    client = TestClient(make_app("run-a"))
    client.get("/answer")

    asyncio.run(fresh_cache.clear())

    assert client.get("/answer").headers["X-Cache"] == "MISS"
```

`fresh_cache.clear_path("/answer")` 則只移除單一路徑；除非傳入 `include_params=True`，否則帶有查詢字串的項目會留下。在應用程式內，handler 可以呼叫 `await invalidate(request)`。

`app.dependency_overrides[get_cache_backend]` 不能取代這個 fixture：它只改變 `CacheBackend` 依賴項回傳的內容，`@cache` 仍然讀取 `BackendProxy`。

## Redis {#redis}

使用網路後端時，還有兩件事要注意。

**在 lifespan 內建立後端。** Redis 用戶端會把連線綁定到它第一次執行所在的事件迴圈（event loop）。在匯入時建立後端、再讓各自執行不同迴圈的測試共用，會因為迴圈已關閉或不同而出錯。在 lifespan 內建立的話，它的壽命就與應用程式實例完全一致，如[關閉後端](BACKENDS.md#closing-a-backend)所示。`TestClient` 只有在當作 context manager 使用時才會執行 lifespan，而搭配 `ASGITransport` 的 `httpx.AsyncClient` 完全不會執行。

**隔離的是鍵，不只是 proxy。** 清除 `BackendProxy` 之後，所有的鍵仍留在 Redis 裡，下一個測試（或下一次執行）會找到它們。讓每個測試使用自己的 `key_prefix`，並在關閉時清除；`clear()` 只會刪除該後端前綴底下的鍵。

```python
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_cachex import BackendProxy, cache
from fastapi_cachex.backends import AsyncRedisCacheBackend


def make_app(answer: str, key_prefix: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        backend = AsyncRedisCacheBackend(
            host="127.0.0.1",
            port=int(os.environ["TEST_REDIS_PORT"]),
            key_prefix=key_prefix,
        )
        BackendProxy.set(backend)
        try:
            yield
        finally:
            BackendProxy.set(None)  # 取消註冊
            try:
                await backend.clear()  # 刪除這個測試的鍵
            finally:
                await backend.aclose()  # 關閉連線池

    app = FastAPI(lifespan=lifespan)

    @app.get("/answer")
    @cache(ttl=60, debug_header=True)
    async def read_answer() -> dict[str, str]:
        return {"answer": answer}

    return app


@pytest.fixture
def key_prefix() -> str:
    return f"test:{uuid.uuid4().hex}:"


@pytest.mark.parametrize("answer", ["run-a", "run-b"])
def test_runs_are_isolated(answer: str, key_prefix: str) -> None:
    with TestClient(make_app(answer, key_prefix)) as client:
        assert client.get("/answer").json() == {"answer": answer}
        assert client.get("/answer").headers["X-Cache"] == "HIT"
```

收尾的三個步驟做的是三件不同的事：取消註冊讓之後的程式碼找不到這個後端，清除會移除它儲存的內容，關閉則釋放它的 socket。省略最後一步，套件結束時會出現未關閉連線的 `ResourceWarning`；`finally` 區塊確保測試失敗或 `clear()` 失敗時也不會跳過它。

請讓這類測試連到專為它們準備的 Redis（由套件或 CI 啟動的容器），絕對不要連到應用程式正在使用的 Redis。在你自己的應用程式中，請從設定讀取前綴，讓測試可以覆寫。Memcached 無法列舉鍵，因此它的 `clear()` 會清空整台伺服器，並以 `RuntimeWarning` 提醒：請給 Memcached 測試一台專用的伺服器；如果你的套件把警告當成錯誤，請用 `pytest.warns` 預期這個警告。

## 檢查清單 {#checklist}

- 一個 autouse fixture 設定全新的 `MemoryBackend`，並在每個測試之後清除 `BackendProxy` 與 `CacheManagerProxy`。
- 有一個測試證明隔離有效：同一個請求、兩個應用程式、兩個答案。
- 網路後端在 lifespan 內建立，測試使用 `with TestClient(app) as client:`。
- 每個測試有自己的 `key_prefix`；收尾時依序取消註冊、清除、關閉。
- 斷言讀取 `X-Cache` 而不是計算 handler 被呼叫的次數，而且不靠 sleep 等 TTL 過去。
