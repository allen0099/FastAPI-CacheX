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
| Session 與 OAuth state 已棄用，0.5.0 移除 | [#420](https://github.com/allen0099/FastAPI-CacheX/issues/420) | 否（0.4.0 會發出 `FutureWarning`） | [Session 與 OAuth state 已棄用](#session-state-deprecated) |
| Session Cookie 預設為 `__Host-session` 並帶 `Secure` | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | `FutureWarning` | [Session Cookie](#session-cookie) |
| 拒絕互相矛盾的 `__Host-`／`__Secure-` Cookie 設定 | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | `UserWarning`（`cookie_name` 維持預設值時為 `FutureWarning`，僅限使用中介軟體時） | [Session Cookie](#session-cookie) |
| 明確的 `login()`／`logout()`、唯讀的 `Session.user` | [#256](https://github.com/allen0099/FastAPI-CacheX/issues/256) | 否 | [登入與登出](#login-logout) |
| `get_or_set()` 預設使用鎖 | [#280](https://github.com/allen0099/FastAPI-CacheX/issues/280) | `FutureWarning` | [get_or_set 的鎖](#get-or-set-lock) |
| 已取消：`get_session_manager` 透過 `SessionManagerProxy` 取得 | [#131](https://github.com/allen0099/FastAPI-CacheX/issues/131) | `FutureWarning`（0.4.0 不再警告） | [get_session_manager](#get-session-manager) |
| 已取消：`token_source_priority` 列出所有權杖來源 | [#75](https://github.com/allen0099/FastAPI-CacheX/issues/75) | `FutureWarning`（0.4.0 不再警告） | [權杖來源](#token-source-priority) |
| `add_routes()` 必須傳入 `dependencies`，預設不含內容預覽 | [#298](https://github.com/allen0099/FastAPI-CacheX/issues/298) | `UserWarning`（僅在省略 `dependencies` 時） | [監控路由](#add-routes) |
| 移除 Redis 的 `encoding` 選項 | [#126](https://github.com/allen0099/FastAPI-CacheX/issues/126) | `DeprecationWarning`（UTF-8 以外的值為 `RuntimeWarning`） | [Redis encoding](#redis-encoding) |
| 拒絕短於雜湊輸出的 JWT HMAC 密鑰 | [#129](https://github.com/allen0099/FastAPI-CacheX/issues/129) | `UserWarning` | [JWT 密鑰長度](#jwt-secret) |
| 移除 `SessionMiddleware` | [#69](https://github.com/allen0099/FastAPI-CacheX/issues/69) | `DeprecationWarning` | [SessionMiddleware](#session-middleware) |
| 移除 `BackendProxy.get_backend()`／`set_backend()` | [#70](https://github.com/allen0099/FastAPI-CacheX/issues/70) | `DeprecationWarning` | [BackendProxy](#backend-proxy) |
| 移除 `CacheError` | [#130](https://github.com/allen0099/FastAPI-CacheX/issues/130) | `DeprecationWarning` | [CacheError](#cache-error) |
| 移除 Redis `clear_pattern()` 去除前綴後的重試 | [#125](https://github.com/allen0099/FastAPI-CacheX/issues/125) | `DeprecationWarning` | [Redis clear_pattern](#redis-clear-pattern) |
| `SessionConfig.use_bearer_token` 維持已棄用，0.5.0 移除 | [#377](https://github.com/allen0099/FastAPI-CacheX/issues/377) | `DeprecationWarning` | [權杖來源](#token-source-priority) |
| `UserSessionDep` 需要使用者 | [#127](https://github.com/allen0099/FastAPI-CacheX/issues/127) | 否 | [UserSessionDep](#user-session-dep) |
| 移除 `memcache` extra | [#202](https://github.com/allen0099/FastAPI-CacheX/issues/202) | 否 | [memcache extra](#memcache-extra) |
| `BaseCacheBackend.delete()` 回傳 `bool` | [#71](https://github.com/allen0099/FastAPI-CacheX/issues/71) | 否（回傳 `None` 時 0.4.0 會發出 `FutureWarning`） | [delete() 的回傳值](#backend-delete) |
| `CacheEntry.headers` 改為成對值的 tuple | [#105](https://github.com/allen0099/FastAPI-CacheX/issues/105) | 否 | [重複的標頭](#cache-entry-headers) |
| HTTP 快取鍵格式 | [#271](https://github.com/allen0099/FastAPI-CacheX/issues/271)、[#270](https://github.com/allen0099/FastAPI-CacheX/issues/270)、[#269](https://github.com/allen0099/FastAPI-CacheX/issues/269)、[#266](https://github.com/allen0099/FastAPI-CacheX/issues/266)、[#265](https://github.com/allen0099/FastAPI-CacheX/issues/265)、[#72](https://github.com/allen0099/FastAPI-CacheX/issues/72) | 否 | [快取鍵](#cache-keys) |
| 有條件的 Session 寫入 | [#128](https://github.com/allen0099/FastAPI-CacheX/issues/128) | 否 | [Session 寫入](#session-writes) |

## Session 與 OAuth state 已棄用 {#session-state-deprecated}

`fastapi_cachex.session` 與 `fastapi_cachex.state` 在 0.4.0 已棄用，並將在 0.5.0 移除（[#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)、[#421](https://github.com/allen0099/FastAPI-CacheX/issues/421)）。FastAPI-CacheX 的範圍將收斂到 HTTP 快取與應用層快取。Session 處理與 OAuth state 都牽涉安全性，由專門的函式庫維護會更好。由於 0.3.9 沒有預告這項變更，兩個套件在整個 0.4.x 期間都能繼續使用，但只會收到安全性修正。

匯入其中任一個套件，或從 `fastapi_cachex` 讀取它們的名稱（例如 `fastapi_cachex.SessionConfig`），都會發出 `FutureWarning`，並指向匯入的那一行。單純 `import fastapi_cachex` 不會發出警告，`@cache`、`CacheManager`、`CacheLock` 與各後端也不會。Session 與 state 的名稱已不在 `fastapi_cachex.__all__` 中，因此 `from fastapi_cachex import *` 不再提供它們。遷移完成前，請以名稱個別匯入。

遷移方向：

| 目前使用 | 改用 |
|----------|------|
| 搭配 Cookie Session 的 `FastAPICacheXSessionMiddleware` | Starlette 的 `SessionMiddleware` 會把少量的 Session 資料存放在簽署過的 Cookie 中。若 Session 必須存放在伺服器端（資料量大，或需要在伺服器端撤銷），請改用伺服器端 Session 函式庫，例如 `starsessions`。 |
| API 以標頭或 `Authorization: Bearer` 傳遞 Session 權杖 | 改用驗證機制本身的存取權杖，例如以 JWT 函式庫驗證的 OAuth 2 Bearer 權杖。 |
| 以 `StateManager` 處理 OAuth/OIDC 的 `state` | 改用 OAuth 用戶端函式庫。例如 Authlib 的 Starlette 整合會替你產生並檢查 `state` 與 `nonce`；它把這些值存放在 `request.session`，因此需要 Starlette 的 `SessionMiddleware`。 |
| `CacheManager`、`CacheLock`、`@cache` | 不需處理，這些並未棄用。 |

後端中已存在的 Session 與 state 不需要清理，會依各自的 TTL 自行過期。

遷移期間若要隱藏警告，請在第一次匯入之前加上篩選：

```python
import warnings

warnings.filterwarnings(
    "ignore", message="fastapi_cachex.session is deprecated", category=FutureWarning
)
warnings.filterwarnings(
    "ignore", message="fastapi_cachex.state is deprecated", category=FutureWarning
)
```

## Session {#sessions}

若在 0.4.x 期間繼續使用 Session，下列變更仍然適用。

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

0.4.0 也會拒絕互相矛盾的設定：`__Host-` 名稱搭配 `cookie_https_only=False`、`"/"` 以外的 `cookie_path` 或 `cookie_domain`，以及 `__Secure-` 名稱搭配 `cookie_https_only=False`。瀏覽器本來就會拒絕這樣的 Cookie，所以 Session 永遠無法保存；0.3.9 在以這些設定建立 `SessionConfig` 時會發出 `UserWarning`。拒絕時 `SessionConfig` 會引發 `pydantic.ValidationError`（屬於 `ValueError`）。由於預設名稱改為 `__Host-session`，只設定 `cookie_https_only=False`、`cookie_path` 或 `cookie_domain`，而讓 `cookie_name` 維持預設值的設定也會引發錯誤。0.3.9 只在使用 `FastAPICacheXSessionMiddleware` 時透過上述 `FutureWarning` 警告這種情況；只使用標頭且設定了其中一個選項的設定不會收到警告。請一併設定 `cookie_name`，如第一個修改後範例所示；若沒有任何地方讀取 Cookie，也可以直接移除該選項。

### 登入與登出 {#login-logout}

0.4.0 讓使用者只能透過一個明確的 API 成為已驗證狀態，而這個 API 一律會發出新的 Session ID（[#256](https://github.com/allen0099/FastAPI-CacheX/issues/256)）：

- `login(request, user)`（0.3.9 已提供，`from fastapi_cachex.session import login`）會附加使用者並輪替 ID。現在就請改用它，而不是自行設定 `session.user`。
- `await logout(request)`（`from fastapi_cachex.session import logout`）會立即從後端刪除 Session，因此在回應送出之前其權杖就已失效，Cookie 用戶端也會收到讓 Cookie 過期的回應。該請求中沒有載入或建立任何 Session 時回傳 `False`。`request.session.clear()` 仍代表登出。
- 指定 `session.user` 會拋出 `AttributeError`。使用者由 `login()` 與 `SessionManager.create_session(user=...)` 設定，或在建立 `Session` 時傳入。使用中介軟體時請改用 `login()`；沒有中介軟體時，請以 `create_session(user=...)` 建立 Session。
- 登入時預設會帶入匿名 Session 的所有資料，因此購物車在登入後仍會保留。`login(request, user, keep=["cart"])` 只帶入列出的鍵，`keep=[]` 則什麼都不帶。傳入字串會拋出 `TypeError`，否則 `keep="cart"` 會被當成它的各個字母。
- `login()` 或 `rotate_session_id()` 輪替 ID 後，舊 ID 立即失效，沒有寬限期：若有寬限期，被植入的權杖在這段期間會解析到已登入的 Session。
- `rotate_session_id()` 保留原名，用於不更換使用者的權限變更。

這些大多是新的 API，而 0.3.x 無從判斷哪些程式碼會指定 `session.user`，因此 0.3.9 不會發出警告。若應用程式依據自己寫入 `request.session` 的鍵（`request.session.get("user_id")`）判斷是否已登入，這不在函式庫能察覺的範圍內；請使用函式庫的身分（`session.user`、`AuthenticatedSession`），或自行輪替 ID。

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

0.3.9 曾預告 0.4.0 會改為只透過 `SessionManagerProxy` 取得 `get_session_manager`（以及使用它的 `SessionManagerDep`、`ClientIPDep` 與 `rotate_session_id()`）（[#131](https://github.com/allen0099/FastAPI-CacheX/issues/131)），並發出 `FutureWarning`。由於 Session 已棄用（[#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)），0.4.0 不會做這項變更：它仍回傳中介軟體存放在 `app.state` 的管理器，也不再發出警告。若你已經呼叫 `SessionManagerProxy.set()`，保留它也沒有影響。

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

使用 `token_format="jwt"` 時，若 `jwt_algorithm` 為 `HS384` 或 `HS512`，而 `secret_key` 以 UTF-8 編碼後短於 48 或 64 位元組（RFC 7518 第 3.2 節），0.4.0 會在啟動時、`SessionManager` 建立 `JWTTokenSerializer` 的當下拋出 `ValueError`（[#129](https://github.com/allen0099/FastAPI-CacheX/issues/129)）。0.3.x 則是在同一處發出 `UserWarning`。請使用較長的密鑰，或改用 `HS256`：

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

0.4.0 會以有條件的方式寫入 Session（[#128](https://github.com/allen0099/FastAPI-CacheX/issues/128)）：若某個請求載入 Session 之後，另一個請求刪除、使其失效或輪替了它，前者儲存時不會再讓紀錄復活。不需要修改程式碼。

- 一般的儲存改為有條件寫入：中介軟體儲存 `request.session` 的修改、滑動續期，以及 `update_session()`。只有在後端的紀錄仍等於這個請求最後一次讀到或寫入的值時才會成功。刪除、使其失效與過期仍無條件執行，因此安全動作永遠優先。
- 被拒絕的儲存會被捨棄並記錄 log。回應照常送出，但不附 Session 權杖。例外是這個請求已儲存的續期：若 Session 仍然有效（這次儲存只是輸給另一次儲存），會送出續期後的權杖，避免 JWT 用戶端持有比紀錄更早過期的權杖。
- 輪替 ID（`regenerate_session_id()`）會以原子操作移除舊紀錄，且只有在該紀錄仍然有效時才繼續，因此在另一個請求刪除、使其失效或輪替 Session 之前讀到的副本，無法以新 ID 復活。否則它會拋出 `SessionNotFoundError` 或 `SessionInvalidError`，不會以新 ID 儲存任何東西（舊紀錄無論如何都會被移除）；同一個 Session 同時被輪替兩次時，只有第一次會成功。期間另一個請求儲存的修改不會阻止輪替。此時 `rotate_session_id()` 回應 `401` 且不送出權杖，`login()` 則改為替使用者建立新的 Session，不帶已結束 Session 的資料。手動建立、從未自後端讀取或寫入的 `Session` 仍照舊輪替。
- **副作用：**兩個請求同時修改同一個 Session 時（例如兩個分頁同時加入購物車），先儲存的成功，後儲存的被捨棄。目前是後儲存的覆蓋先儲存的，本來就會遺失其中一筆修改；0.4.0 改變的是遺失哪一筆。合併這類修改的做法由 [#376](https://github.com/allen0099/FastAPI-CacheX/issues/376) 追蹤。
- `update_session()` 現在會回傳值：已儲存時為 `True`，儲存被捨棄時為 `False`。手動建立、從未自後端讀取的 `Session`，只有在其 ID 底下尚無紀錄時才會被儲存。
- `get_session()` 續期 Session（滑動過期）時，若續期與另一次儲存競爭而失敗，會重新讀取 Session 並重試一次。若再次失敗，這個請求沿用它讀到的 Session，且不送出續期後的權杖。
- `Session` 的相等比較不考慮各副本最後讀到或寫入的後端項目，因此載入的 Session 仍等於以相同欄位建立的 Session。
- 後端新增 `set_if_equals(key, expected, value, ttl=None)`，與 `delete_if_equals`、`expire_if_equals` 同一系列。基底類別提供不具原子性的預設實作，因此自訂後端不需修改也能運作；覆寫它才能讓儲存具有原子性。

### 權杖來源 {#token-source-priority}

0.3.9 在這裡預告了兩項變更。由於 Session 已棄用（[#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)），0.4.0 兩項都不會做：

- **取消 `token_source_priority` 列出所有權杖來源（[#75](https://github.com/allen0099/FastAPI-CacheX/issues/75)）。** 不論清單是否列出 `"cookie"`，中介軟體仍會在標頭來源之後讀取 Session Cookie，且 `"cookie"` 仍只能放在最後一項。0.4.0 不再對沒有 `"cookie"` 的清單發出 `FutureWarning`。已經以 `"cookie"` 結尾的清單可以照常使用。
- **0.4.0 不會移除 `use_bearer_token`（[#377](https://github.com/allen0099/FastAPI-CacheX/issues/377)）。** 它維持已棄用，傳入時仍會發出 `DeprecationWarning`，並在 0.5.0 隨 `fastapi_cachex.session` 一起移除。是否讀取 Bearer 權杖只由清單決定：

```python
# 之前
SessionConfig(secret_key=SECRET, use_bearer_token=False)

# 之後
SessionConfig(secret_key=SECRET, token_source_priority=["header", "cookie"])
```

`use_bearer_token=True` 是預設值，直接拿掉即可。

## 應用層快取 {#application-cache}

### get_or_set 的鎖 {#get-or-set-lock}

0.4.0 起，`CacheManager.get_or_set()` 預設開啟 cache stampede 保護（[#280](https://github.com/allen0099/FastAPI-CacheX/issues/280)）：同一個鍵同時未命中時，只有一個呼叫者執行 `factory`，其他呼叫者等待它的結果。這會讓未命中時多出後端往返：沒有其他呼叫者等待時，在 Redis 與 Memcached 上是六次而非兩次，另外每個等待中的呼叫者每次輪詢兩次（見[成本](APP_CACHE.md#cost)）。命中時不變。在 0.3.9 中，若 `get_or_set()` 呼叫沒有傳入 `lock=`，而 manager 建立時也沒有傳入 `lock=`，每個 manager 會發出一次 `FutureWarning`。`AppCache` 自動建立的 manager 也包含在內。在 0.3.9 表示「未選擇」的 `CacheManager(lock=None)` 會引發 `TypeError`，請傳入 bool。`get_or_set(lock=None)` 仍沿用 manager 的設定。等待中的呼叫者現在會阻塞到第一個呼叫者的 `factory` 回傳為止，上限為 `lock_ttl`（預設 60 秒）；可傳入 `wait_timeout=` 限制等待時間（見 [Cache stampede 保護](APP_CACHE.md#stampede-protection)）。`factory` 執行期間，後端會保存一個短期的鎖鍵 `lock:<prefix><key>`，`get_all_keys()` 與監控路由都會列出它。

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
- 鍵以格式標籤 `http:v2|`（`CacheKey.FORMAT_TAG`）開頭，讓下一次格式變更可以用模式移除舊鍵：`clear_pattern("http:v2|*")` 會移除這個格式的所有鍵。
- host 會正規化：轉為小寫，並去除空的連接埠或該 scheme 的預設連接埠（http 為 `:80`，https 為 `:443`）。
- 超過 200 位元組（以鍵中的編碼計算）的查詢字串會以 `sha256:` 加上十六進位摘要儲存；路徑仍保持可讀，監控路由則顯示這個摘要。
- 查詢參數預設會依名稱排序，適用於 `@cache`、`invalidate()`、`build_cache_key()` 與 `CacheKey.from_request()`（`sort_query`，0.3.9 起可選用），因此 `?b=2&a=1` 與 `?a=1&b=2` 共用同一筆項目。
- 由單一、公開的 `CacheKey` 型別負責建立、編碼與解析鍵。`fastapi_cachex.routes` 中的 `CACHE_KEY_MIN_PARTS`、`CACHE_KEY_MAX_SPLIT` 與 `CACHE_KEY_MAX_PARTS` 已移除；請改用 `CacheKey.parse(key)` 讀取鍵的各段。

```text
修改前：GET|||Example.com:80|||/users/1|||page=2
修改後：http:v2|GET|example.com|/users/1|page=2
```

需要修改的地方：

- 直接寫出分隔符號的 `clear_pattern()` 模式（`"GET|||*|||/users/*"`）需要改寫，以大寫或帶預設連接埠寫出 host 的模式也一樣。`clear_path()` 與 `invalidate()` 會自行組出鍵，不需要修改。
- 呼叫 `build_cache_key()` 的自訂 `key_builder` 會自動跟上，包括排序；若要保留送出的順序，請對 `build_cache_key()` 傳入 `sort_query=False`。`@cache` 與 `invalidate()` 現在會拒絕與自訂 `key_builder` 一起傳入的 `sort_query`，`False` 也包括在內（0.3.9 接受在此傳入 `False`，但它沒有作用）：請移除它，由 builder 決定。自行組出鍵的（以 `CACHE_KEY_SEPARATOR` 串接或直接寫死 `|||`）仍可以快取，也仍能搭配 `invalidate()` 與 `clear_pattern()`，但其鍵沒有 `http:v2` 標籤，因此 `clear_path()` 不再找得到它們，監控路由也不再列出它們。改用 `build_cache_key(request, *components)` 即可兩者都保留。
- 回應取決於用戶端送出的查詢字串順序的處理函式（例如從 `request.url` 複製的自身連結或分頁連結、對原始查詢字串計算的簽章），請設定 `@cache(sort_query=False)`，並對該路由的 `invalidate()` 傳入 `sort_query=False`。否則第一位呼叫者的順序會被快取，並提供給送出其他順序的呼叫者。`sort_query=False` 在 0.3.9 就能使用。
- 0.4.0 不會讀取 0.3.x 寫入的項目。這些項目會在 TTL 到期後過期；在 Redis 與記憶體後端上，可以在升級後立即以 `await backend.clear_pattern("*|||*")` 移除。這個模式會比對任何含有 `|||` 的鍵，因此請先確認你自己的鍵（例如 `CacheManager` 的鍵）都不含它。Memcached 無法列舉鍵，只能等它們過期。

0.3.9 不會警告：0.3.x 無從判斷某個模式或 key builder 是否符合新格式，而執行期唯一的代價只是一次未命中。與自訂 `key_builder` 一起傳入的 `sort_query` 同樣不會警告，但它會在套用裝飾器時（通常是匯入時）拋出 `CacheXError`，而不是在請求時。

### 重複的標頭 {#cache-entry-headers}

0.4.0 會儲存 handler 重複送出的標頭（例如多個 `Link` 標頭）的每一行，而不是只保留最後一行（[#105](https://github.com/allen0099/FastAPI-CacheX/issues/105)）。`CacheEntry.headers` 從 `dict[str, str] | None` 改為依送出順序排列的 `(name, value)` 成對值 tuple；沒有標頭時為 `()`。重播的值不再合併或拆分，因此含有逗號的值仍是一行。這會影響自訂後端，以及讀取 `CacheEntry` 的程式碼。建立 `CacheEntry` 的程式碼仍可傳入 `dict`（或任何成對值的 iterable，或 `None`）：建構函式會轉換它，但型別檢查器會標出 `dict`。

```python
# 修改前
entry.headers["link"]

# 修改後
[value for name, value in entry.headers if name == "link"]
```

Redis 與 Memcached 以 `[name, value]` 行組成的 JSON 清單儲存標頭。0.4.0 仍能解碼 0.3.x 寫入的物件，但 0.3.x 遇到 0.4.0 寫入且帶有任何標頭的項目時會回應 `500`。使用預設 key builder 或呼叫 `build_cache_key()` 的 builder 時，`@cache` 的滾動部署不需要額外處理：[快取鍵格式](#cache-keys)在同一個版本中改變，因此兩個版本都不會查到對方寫入的 HTTP 項目。自行組出鍵的自訂 `key_builder` 在兩個版本中產生相同的鍵；對這樣的路由，請在部署期間讓 0.3.x 實例不要使用共用後端，或先把 builder 改為使用 `build_cache_key()`。以自己的鍵儲存 `CacheEntry` 並在不同版本間共用的應用程式也是如此。

### 監控路由 {#add-routes}

0.4.0 起 `add_routes()` 必須傳入 `dependencies`，`include_content_preview` 預設為 `False`（[#298](https://github.com/allen0099/FastAPI-CacheX/issues/298)）。0.3.9 在省略 `dependencies` 時會發出 `UserWarning`。`dependencies` 與 `include_content_preview` 只能以關鍵字傳入；省略 `dependencies` 或傳入 `None` 會引發 `TypeError`。0.3.9 對以下兩種情況不會發出警告：以位置傳入這些參數，現在會引發 `TypeError`；以及已傳入 `dependencies` 但依賴預覽的預設值，現在預覽會被隱藏，請傳入 `include_content_preview=True` 保留。

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

0.4.0 的 Redis 用戶端會直接讀取原始位元組，並從 `AsyncRedisCacheBackend` 與 `RedisConfig` 移除 `encoding` 選項（[#126](https://github.com/allen0099/FastAPI-CacheX/issues/126)）。項目一律以 UTF-8 寫入，因此省略它不會改變任何行為。在 0.3.9 中，傳給 `AsyncRedisCacheBackend` 的 UTF-8 `encoding` 會發出 `DeprecationWarning`，其他值則只會發出原有的 `RuntimeWarning`，其訊息同樣預告此參數將被移除。設定了 `encoding` 的 `RedisConfig` 在傳入 `load_from_config()` 時會發出 `DeprecationWarning`，若值不是 UTF-8 還會另外發出 `RuntimeWarning`。在 0.4.0 中，傳入 `encoding` 或 `decode_responses` 給 `AsyncRedisCacheBackend` 會引發 `TypeError`，而 `RedisConfig` 會像對待其他未知欄位一樣忽略 `encoding` 值。`decode_responses` 在 0.3.9 中不會發出警告：它一向只接受預設值 `True`，因此傳入它不會有任何效果。

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
await backend.clear_pattern("GET|||*")  # 在 0.4.0 的鍵格式下為 "http:v2|GET|*"
```

### delete() 的回傳值 {#backend-delete}

0.4.0 起 `BaseCacheBackend.delete()` 回傳是否移除了鍵，而不是 `None`（[#71](https://github.com/allen0099/FastAPI-CacheX/issues/71)），基底類別的非原子性後備實作也會使用這個結果：`delete_many()` 改為計算實際存在的鍵，而不是嘗試刪除的鍵；`get_and_delete()` 與 `delete_if_equals()` 只在呼叫者的刪除確實移除了鍵時才算成功。仍宣告 `-> None` 的第三方後端無法通過型別檢查；執行時，後備實作會像 0.3.x 一樣把 `None` 視為已移除，並發出 `FutureWarning`。0.5.0 會把 `None` 視為 `False`。0.3.9 不會警告：子類別在該版本無法宣告 `-> bool` 而不與 0.3.x 的基底類別產生型別錯誤。

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
