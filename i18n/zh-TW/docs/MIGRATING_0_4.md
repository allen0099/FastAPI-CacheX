# 遷移至 0.4.0 {#migrating-to-040}

0.3.9 是最後一個 0.3.x 版本。0.4.0 包含破壞性變更，收錄於 [0.4.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.4.0)。本頁逐一列出這些變更、需要修改的地方，以及 0.3.9 是否已經發出警告。部分 0.4.0 的設計尚未定案；若 issue 對某個細節仍未決定，該節會說明目前已知與尚未決定的部分。

## 升級前 {#before-you-upgrade}

請先升級到 0.3.9，並在執行測試時將函式庫的警告轉為錯誤：

```bash
python -W error::DeprecationWarning -W error::FutureWarning -m pytest
```

或使用 pytest 本身的設定：

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error::DeprecationWarning",
    "error::FutureWarning",
]
```

下方每個警告都會指出要修改的設定，並附上對應 issue 的連結。`FutureWarning` 預告會改變行為的預設值，預設就會顯示；`DeprecationWarning` 預告移除；`UserWarning` 標示目前就已經有問題、0.4.0 不再接受的設定。0.3.9 不再發出這些警告後，「0.3.9 是否警告」欄位中有警告的變更就已處理完畢；本頁其餘部分說明警告無法偵測的變更。

## 總覽 {#summary}

| 變更 | Issue | 0.3.9 是否警告 | 章節 |
|------|-------|----------------|------|
| Session Cookie 預設為 `__Host-session` 並帶 `Secure` | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | `FutureWarning` | [Session Cookie](#session-cookie) |
| 拒絕互相矛盾的 `__Host-`／`__Secure-` Cookie 設定 | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | `UserWarning` | [Session Cookie](#session-cookie) |
| 明確的 `login()`／`logout()`、唯讀的 `Session.user` | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | 否 | [登入與登出](#login-logout) |
| `get_or_set()` 預設使用鎖 | [#280](https://github.com/allen0099/FastAPI-CacheX/issues/280) | `FutureWarning` | [get_or_set 的鎖](#get-or-set-lock) |
| `get_session_manager` 透過 `SessionManagerProxy` 取得 | [#131](https://github.com/allen0099/FastAPI-CacheX/issues/131) | `FutureWarning` | [get_session_manager](#get-session-manager) |
| `add_routes()` 必須傳入 `dependencies`，預設不含內容預覽 | [#298](https://github.com/allen0099/FastAPI-CacheX/issues/298) | `UserWarning` | [監控路由](#add-routes) |
| 移除 Redis 的 `encoding` 選項 | [#126](https://github.com/allen0099/FastAPI-CacheX/issues/126) | `DeprecationWarning` | [Redis encoding](#redis-encoding) |
| 拒絕短於雜湊輸出的 JWT HMAC 密鑰 | [#129](https://github.com/allen0099/FastAPI-CacheX/issues/129) | `UserWarning` | [JWT 密鑰長度](#jwt-secret) |
| 移除 `SessionMiddleware` | [#69](https://github.com/allen0099/FastAPI-CacheX/issues/69) | `DeprecationWarning` | [SessionMiddleware](#session-middleware) |
| 移除 `BackendProxy.get_backend()`／`set_backend()` | [#70](https://github.com/allen0099/FastAPI-CacheX/issues/70) | `DeprecationWarning` | [BackendProxy](#backend-proxy) |
| 移除 `CacheError` | [#130](https://github.com/allen0099/FastAPI-CacheX/issues/130) | `DeprecationWarning` | [CacheError](#cache-error) |
| 移除 Redis `clear_pattern()` 去除前綴後的重試 | [#125](https://github.com/allen0099/FastAPI-CacheX/issues/125) | `DeprecationWarning` | [Redis clear_pattern](#redis-clear-pattern) |
| `UserSessionDep` 需要使用者 | [#127](https://github.com/allen0099/FastAPI-CacheX/issues/127) | 否 | [UserSessionDep](#user-session-dep) |
| 移除 `memcache` extra | [#202](https://github.com/allen0099/FastAPI-CacheX/issues/202) | 否 | [memcache extra](#memcache-extra) |
| `BaseCacheBackend.delete()` 回傳 `bool` | [#71](https://github.com/allen0099/FastAPI-CacheX/issues/71) | 否 | [delete() 的回傳值](#backend-delete) |
| `CacheEntry.headers` 改為成對值的清單 | [#105](https://github.com/allen0099/FastAPI-CacheX/issues/105) | 否 | [重複的標頭](#cache-entry-headers) |
| HTTP 快取鍵格式 | [#271](https://github.com/allen0099/FastAPI-CacheX/issues/271)、[#270](https://github.com/allen0099/FastAPI-CacheX/issues/270)、[#269](https://github.com/allen0099/FastAPI-CacheX/issues/269)、[#266](https://github.com/allen0099/FastAPI-CacheX/issues/266)、[#265](https://github.com/allen0099/FastAPI-CacheX/issues/265)、[#72](https://github.com/allen0099/FastAPI-CacheX/issues/72) | 否 | [快取鍵](#cache-keys) |
| 有條件的 Session 寫入 | [#128](https://github.com/allen0099/FastAPI-CacheX/issues/128) | 否 | [Session 寫入](#session-writes) |
| `token_source_priority` 可包含 Cookie | [#75](https://github.com/allen0099/FastAPI-CacheX/issues/75) | 否 | [權杖來源](#token-source-priority) |

## Session {#sessions}

### Session Cookie 預設值 {#session-cookie}

0.4.0 會改變兩個 `SessionConfig` 預設值（[#256](https://github.com/allen0099/FastAPI-CacheX/issues/256)）：`cookie_name` 從 `"session"` 改為 `"__Host-session"`，`cookie_https_only` 從 `False` 改為 `True`。瀏覽器只接受帶 `Secure`、`Path=/` 且沒有 `Domain` 的 `__Host-` Cookie，也不接受子網域設定的這種 Cookie，因此移除了植入 Session Cookie 最常見的途徑。這帶來兩個後果：

- 升級後，所有持有 `session` Cookie 的瀏覽器都會被登出一次，因為中介軟體改為尋找 `__Host-session`。
- `Secure` Cookie 不會透過純 HTTP 傳送，因此沒有 TLS 的本機開發環境需要明確關閉它。

在 0.3.9 中，若 `FastAPICacheXSessionMiddleware` 的設定讓 `cookie_name` 或 `cookie_https_only` 維持預設值，會發出 `FutureWarning`。只使用標頭的設定（已棄用的 `SessionMiddleware`，或不搭配中介軟體使用的 `SessionManager`）永遠不會送出 Cookie，因此不會警告。

修改前：

```python
config = SessionConfig(secret_key=SECRET)
app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=SessionManager(backend, config)
)
```

修改後，保留目前的 Cookie（同一段程式碼在 0.3.9 與 0.4.0 都能運作，也是純 HTTP 開發環境需要的設定）：

```python
config = SessionConfig(
    secret_key=SECRET, cookie_name="session", cookie_https_only=False
)
```

修改後，現在就切換（僅限 HTTPS）：

```python
config = SessionConfig(
    secret_key=SECRET, cookie_name="__Host-session", cookie_https_only=True
)
```

0.4.0 也會拒絕互相矛盾的設定：`__Host-` 名稱搭配 `cookie_https_only=False`、`"/"` 以外的 `cookie_path` 或 `cookie_domain`，以及 `__Secure-` 名稱搭配 `cookie_https_only=False`。瀏覽器本來就會拒絕這樣的 Cookie，所以 Session 永遠無法保存；0.3.9 在以這些設定建立 `SessionConfig` 時會發出 `UserWarning`。

### 登入與登出 {#login-logout}

0.4.0 讓使用者只能透過一個明確的 API 成為已驗證狀態，而這個 API 一律會發出新的 Session ID（[#256](https://github.com/allen0099/FastAPI-CacheX/issues/256)）。目前已知：

- `login(request, user)`（0.3.9 已提供，`from fastapi_cachex.session import login`）會附加使用者並輪替 ID。現在就請改用它，而不是自行設定 `session.user`。
- 新增登出 API；`request.session.clear()` 仍代表登出。
- `Session.user` 在這些呼叫之外變成唯讀。直接指定它的程式碼會失效。
- 登入會開始一個新的 Session，而不是將匿名 Session 升級。要保留哪些資料（預設全部，或使用 `keep=` 清單）尚未決定。
- 輪替後的幾秒內，舊 ID 會解析到新的 Session，讓仍帶著舊權杖的進行中請求不會失敗。
- `rotate_session_id()` 會保留，用於不更換使用者的權限變更，名稱可能會改變。

確切的函式簽章尚未定案，因此 0.3.9 不會針對這些變更發出警告。若應用程式依據自己寫入 `request.session` 的鍵（`request.session.get("user_id")`）判斷是否已登入，這不在函式庫能察覺的範圍內；請使用函式庫的身分（`session.user`、`AuthenticatedSession`），或自行輪替 ID。

修改前：

```python
session, _ = await manager.get_session(token)
session.user = SessionUser(user_id=user_id)  # 0.4.0 起唯讀
await manager.update_session(session)
```

修改後（在 0.3.9 可用，也會輪替 Session ID）：

```python
from fastapi_cachex.session import login

await login(request, SessionUser(user_id=user_id))
```

### get_session_manager {#get-session-manager}

`get_session_manager`（以及使用它的 `SessionManagerDep`、`ClientIPDep` 與 `rotate_session_id()`）目前回傳中介軟體存放在 `app.state` 的管理器。0.4.0 改為只透過 `SessionManagerProxy` 取得，與 `BackendProxy` 和 `CacheManagerProxy` 一致（[#131](https://github.com/allen0099/FastAPI-CacheX/issues/131)）。在 0.3.9 中，當 proxy 沒有管理器或持有不同的管理器時，每個應用程式會發出一次 `FutureWarning`。

修改前：

```python
session_manager = SessionManager(backend, config)
app.add_middleware(FastAPICacheXSessionMiddleware, session_manager=session_manager)
```

修改後：

```python
session_manager = SessionManager(backend, config)
SessionManagerProxy.set(session_manager)
app.add_middleware(FastAPICacheXSessionMiddleware)  # 從 proxy 取得管理器
```

### UserSessionDep {#user-session-dep}

`UserSessionDep` 是 `SessionDep` 的別名，也接受匿名 Session。0.4.0 起它與 `AuthenticatedSession` 一樣需要帶有使用者的 Session（[#127](https://github.com/allen0099/FastAPI-CacheX/issues/127)），因此匿名請求存取使用它的路由會得到 `401`。0.3.9 不會警告：型別別名在被使用時沒有可以執行程式碼的時機，而在匯入時警告會對每個人觸發。請選擇符合你本意的依賴項：

```python
# 修改前
async def cart(session: UserSessionDep): ...


# 修改後：允許匿名 Session（目前的行為）
async def cart(session: SessionDep): ...


# 修改後：需要已登入的使用者（0.4.0 的 UserSessionDep）
async def profile(session: AuthenticatedSession): ...
```

### SessionMiddleware {#session-middleware}

只使用標頭的 `SessionMiddleware` 會被移除（[#69](https://github.com/allen0099/FastAPI-CacheX/issues/69)）；0.3.x 已經會發出 `DeprecationWarning`。請改用 `FastAPICacheXSessionMiddleware`，它同樣讀取標頭與 `Authorization: Bearer` 權杖，並提供 `request.session`。它也會對沒有送出權杖的用戶端送出 Session Cookie，因此請依照 [Session Cookie](#session-cookie) 設定 Cookie 選項。另請參閱 [Session 管理](SESSION.md#migration-sessionmiddleware-fastapicachexsessionmiddleware)。

```python
# 修改前
app.add_middleware(SessionMiddleware, session_manager=manager, config=config)

# 修改後
app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=manager, config=config
)
```

### JWT 密鑰長度 {#jwt-secret}

使用 `token_format="jwt"` 時，若 `jwt_algorithm` 為 `HS384` 或 `HS512`，而 `secret_key` 短於 48 或 64 位元組（RFC 7518 第 3.2 節），0.4.0 會在啟動時拋出例外（[#129](https://github.com/allen0099/FastAPI-CacheX/issues/129)）。0.3.x 在建立 `JWTTokenSerializer` 時會發出 `UserWarning`。請使用較長的密鑰，或改用 `HS256`：

```python
# 修改前：32 個字元，對 HS512 來說太短
SessionConfig(
    secret_key=secrets.token_urlsafe(24), token_format="jwt", jwt_algorithm="HS512"
)

# 修改後
SessionConfig(
    secret_key=secrets.token_urlsafe(64), token_format="jwt", jwt_algorithm="HS512"
)
```

### Session 寫入 {#session-writes}

0.4.0 會以有條件的方式寫入 Session（[#128](https://github.com/allen0099/FastAPI-CacheX/issues/128)）：若某個請求載入 Session 之後，另一個請求刪除、使其失效或輪替了它，前者儲存時不會再讓紀錄復活。不需要修改程式碼。自訂後端必須支援這項變更新增的「存在才寫入」原語，其形式尚未決定。

### 權杖來源 {#token-source-priority}

0.4.0 起，`SessionConfig.token_source_priority` 除了 `"header"` 與 `"bearer"` 之外也接受 `"cookie"`（[#75](https://github.com/allen0099/FastAPI-CacheX/issues/75)），`FastAPICacheXSessionMiddleware` 會依照清單順序尋找權杖。現有的清單仍可運作；目前 Cookie 會在標頭與 bearer 來源之後讀取。預設清單是否加入 `"cookie"`、放在什麼位置，尚未決定。

## 應用層快取 {#application-cache}

### get_or_set 的鎖 {#get-or-set-lock}

0.4.0 起，`CacheManager.get_or_set()` 預設開啟 cache stampede 保護（[#280](https://github.com/allen0099/FastAPI-CacheX/issues/280)）：同一個鍵同時未命中時，只有一個呼叫者執行 `factory`，其他呼叫者等待它的結果。這會讓未命中時多出幾次後端往返（見 [Cache stampede 保護](APP_CACHE.md#stampede-protection)）。在 0.3.9 中，若 `get_or_set()` 呼叫沒有傳入 `lock=`，而 manager 建立時也沒有傳入 `lock=`，每個 manager 會發出一次 `FutureWarning`。`AppCache` 自動建立的 manager 也包含在內。

修改前：

```python
manager = CacheManager(key_prefix="myapp:")
value = await manager.get_or_set("report", build_report)
```

修改後，可以針對 manager 或單次呼叫設定：

```python
manager = CacheManager(key_prefix="myapp:", lock=False)  # 保留目前的行為
manager = CacheManager(key_prefix="myapp:", lock=True)  # 現在就啟用

value = await manager.get_or_set("report", build_report, lock=True)
```

使用 `AppCache` 時，請在啟動時註冊自己的 manager：

```python
CacheManagerProxy.set(CacheManager(lock=True))
```

## HTTP 快取 {#http-caching}

### 快取鍵 {#cache-keys}

0.4.0 會一次改變所有 HTTP 快取鍵的格式，讓升級只造成一次快取未命中（[#271](https://github.com/allen0099/FastAPI-CacheX/issues/271)、[#266](https://github.com/allen0099/FastAPI-CacheX/issues/266)、[#265](https://github.com/allen0099/FastAPI-CacheX/issues/265)、[#269](https://github.com/allen0099/FastAPI-CacheX/issues/269)、[#270](https://github.com/allen0099/FastAPI-CacheX/issues/270)、[#72](https://github.com/allen0099/FastAPI-CacheX/issues/72)）：

- 分隔符號改為單一的 `|`（`CACHE_KEY_SEPARATOR`）。
- 鍵以格式標籤開頭，例如 `http:v2|`，讓下一次格式變更可以用模式移除舊鍵。
- 主機名稱會正規化：轉為小寫，並去除該 scheme 的預設連接埠（`:80`、`:443`）。
- 過長的查詢字串（約超過 200 位元組）會以 `sha256:` 加上十六進位摘要儲存；路徑仍保持可讀。
- 查詢參數會排序，如同 0.3.9 起的 `@cache(sort_query=True)`；這會成為預設還是維持選用，尚未決定。
- 由單一的 `CacheKey` 型別負責編碼與解析鍵；`routes.py` 中解析鍵的內部實作會改變。

```text
修改前：GET|||Example.com:80|||/users/1|||page=2
修改後：http:v2|GET|example.com|/users/1|page=2   （確切的標籤尚未定案）
```

需要修改的地方：

- 直接寫出分隔符號的 `clear_pattern()` 模式（`"GET|||*|||/users/*"`）需要改寫。`clear_path()` 與 `invalidate()` 會自行組出鍵，不需要修改。
- 呼叫 `build_cache_key()` 或以 `CACHE_KEY_SEPARATOR` 串接的自訂 `key_builder` 會自動跟上；直接寫死 `|||` 的則不會。
- 0.4.0 不會讀取 0.3.x 寫入的項目。這些項目會在 TTL 到期後過期；在 Redis 與記憶體後端上，可以在升級後立即以 `await backend.clear_pattern("*|||*")` 移除。Memcached 無法列舉鍵，只能等它們過期。

0.3.9 不會警告：0.3.x 無從判斷某個模式或 key builder 是否符合新格式，而執行期唯一的代價只是一次未命中。

### 重複的標頭 {#cache-entry-headers}

0.4.0 會儲存 handler 重複送出的標頭（例如多個 `Link` 標頭）的每一行，而不是只保留最後一行（[#105](https://github.com/allen0099/FastAPI-CacheX/issues/105)）。`CacheEntry.headers` 從 `dict[str, str]` 改為有順序的 `(name, value)` 成對值清單。這會影響自訂後端，以及建立或讀取 `CacheEntry` 的程式碼：

```python
# 修改前
entry.headers["link"]

# 修改後
[value for name, value in entry.headers if name == "link"]
```

0.4.0 仍可讀取 0.3.x 寫入的項目，但 0.3.x 無法讀取 0.4.0 寫入的項目。在兩個版本共用同一個後端的滾動部署中，請在所有實例都執行 0.4.0 後清除一次 HTTP 快取（或在部署期間讓舊實例不要使用共用快取）。改由儲存格式帶版本標記是否可行，尚未決定。

### 監控路由 {#add-routes}

0.4.0 起 `add_routes()` 必須傳入 `dependencies`，`include_content_preview` 預設為 `False`（[#298](https://github.com/allen0099/FastAPI-CacheX/issues/298)）。0.3.x 在省略 `dependencies` 時會發出 `UserWarning`。

```python
# 修改前
add_routes(app)

# 修改後
add_routes(app, dependencies=[Depends(verify_admin)])
# 或刻意不加保護（本機或測試環境），並保留預覽：
add_routes(app, dependencies=[], include_content_preview=True)
```

## 後端 {#backends}

### Redis encoding {#redis-encoding}

0.4.0 的 Redis 用戶端會直接讀取原始位元組，並從 `AsyncRedisCacheBackend` 與 `RedisConfig` 移除 `encoding` 選項（[#126](https://github.com/allen0099/FastAPI-CacheX/issues/126)）。項目一律以 UTF-8 寫入，因此省略它不會改變任何行為。在 0.3.9 中，只要傳入 `encoding` 就會發出 `DeprecationWarning`（UTF-8 以外的值仍會另外發出 `RuntimeWarning`）。

```python
# 修改前
AsyncRedisCacheBackend(host="redis", encoding="utf-8")
RedisConfig(host="redis", encoding="utf-8")

# 修改後
AsyncRedisCacheBackend(host="redis")
RedisConfig(host="redis")
```

### Redis clear_pattern {#redis-clear-pattern}

0.3.8 之前，以後端 `key_prefix` 開頭的 Redis `clear_pattern()` 模式會在去除前綴後比對。0.3.x 在模式沒有清除任何項目時仍會以這種方式重試，並發出 `DeprecationWarning`；0.4.0 移除這個重試（[#125](https://github.com/allen0099/FastAPI-CacheX/issues/125)）。模式比對的是邏輯上的鍵：

```python
# 修改前
await backend.clear_pattern("fastapi_cachex:GET|||*")

# 修改後
await backend.clear_pattern("GET|||*")  # 在 0.4.0 的鍵格式下為 "GET|*"
```

### delete() 的回傳值 {#backend-delete}

0.4.0 起 `BaseCacheBackend.delete()` 回傳是否移除了鍵，而不是 `None`（[#71](https://github.com/allen0099/FastAPI-CacheX/issues/71)），基底類別的 `delete_many()` 後備實作也改為計算實際存在的鍵，而不是嘗試刪除的鍵。回傳 `None` 的第三方後端無法通過型別檢查，也會讓這個計數出錯。0.3.9 不會警告：子類別目前無法宣告 `-> bool` 而不與 0.3.x 的基底類別產生型別錯誤，而且沒有人使用這個 `None`。

```python
# 修改前
class MyBackend(BaseCacheBackend):
    async def delete(self, key: str) -> None:
        await self._client.delete(key)


# 修改後
class MyBackend(BaseCacheBackend):
    async def delete(self, key: str) -> bool:
        return await self._client.delete(key) > 0
```

在 0.3.x 需要知道結果的呼叫者，可以使用 `await backend.get_and_delete(key) is not None`。

### BackendProxy {#backend-proxy}

`BackendProxy.get_backend()` 與 `set_backend()` 會被移除（[#70](https://github.com/allen0099/FastAPI-CacheX/issues/70)）；0.3.x 已經會發出 `DeprecationWarning`。

```python
# 修改前
BackendProxy.set_backend(backend)
backend = BackendProxy.get_backend()

# 修改後
BackendProxy.set(backend)
backend = BackendProxy.get()
```

### CacheError {#cache-error}

`CacheError` 別名會被移除（[#130](https://github.com/allen0099/FastAPI-CacheX/issues/130)）；0.3.x 在匯入它時已經會發出 `DeprecationWarning`。

```python
# 修改前
from fastapi_cachex.exceptions import CacheError

# 修改後
from fastapi_cachex.exceptions import CacheXError
```

### memcache extra {#memcache-extra}

`memcache` extra（`memcached` 的別名）會被移除（[#202](https://github.com/allen0099/FastAPI-CacheX/issues/202)）。這個失敗很安靜：pip 與 uv 對未知的 extra 只會警告，並在沒有 `pymemcache` 的情況下安裝 `fastapi-cachex`，因此錯誤要到建立 `MemcachedBackend` 時才會出現。0.3.9 無法警告，因為 extra 由安裝工具解析，函式庫看不到安裝時指定了哪一個。

```bash
# 修改前
pip install "fastapi-cachex[memcache]"

# 修改後
pip install "fastapi-cachex[memcached]"
```
