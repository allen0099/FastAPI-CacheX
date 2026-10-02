# Session 管理擴充 {#session-management-extension}

> [!WARNING]
> **已棄用。** `fastapi_cachex.session` 在 0.4.0 已棄用，並將在 0.5.0 移除（[#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)）。匯入時會發出 `FutureWarning`。遷移方向請見[遷移至 0.4.0](MIGRATING_0_4.md#session-state-deprecated)。

FastAPI-CacheX 的 Session 管理提供完整的使用者 Session 處理，包括簽署過的權杖、滑動過期，以及可選的 IP / User-Agent 綁定。Session 內容一律存放在快取後端；用戶端只持有一個簽署過的權杖。

**`FastAPICacheXSessionMiddleware` 如何傳遞權杖：**

| 權杖來源 | 回應端 |
|--------------|---------------|
| 自訂標頭（預設 `X-Session-Token`）／`Authorization: Bearer`／**Cookie**（預設名稱 `session`） | 依來源決定：送出標頭或 Bearer 權杖的請求（即使該權杖已無法解析）會在回應標頭中收到權杖；其他情況（Cookie，或完全沒有權杖）則使用 `Set-Cookie` |

只支援標頭的 `SessionMiddleware` 自 0.3.1 起已棄用，**已於 0.4.0 移除**；見[遷移](#migration-sessionmiddleware-fastapicachexsessionmiddleware)。

`SessionConfig` 的六個 `cookie_*` 設定（`cookie_name`、`cookie_max_age`、`cookie_path`、`cookie_same_site`、`cookie_https_only`、`cookie_domain`）**只有 `FastAPICacheXSessionMiddleware` 會讀取**；不透過它而直接使用 `SessionManager` 時，設定它們不會有任何效果，但 `SessionConfig` 仍會驗證它們（見 [Cookie 預設值](#cookie-defaults)）。

完整可執行範例（英文）：[`examples/session_login.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_login.py)、[`examples/session_jwt.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt.py)。

## 功能特點 {#features}

- ✅ **Session 生命週期管理**：建立、讀取、更新、刪除、使其失效
- ✅ **安全性**：
    - HMAC-SHA256 權杖簽署
    - IP 位址綁定（可選）
    - User-Agent 綁定（可選）
    - 登入後重新產生 Session ID
- ✅ **多種權杖來源**：自訂標頭、`Authorization: Bearer`、Cookie（只有 `FastAPICacheXSessionMiddleware` 支援 Cookie）
- ✅ **可選的 JWT 格式**：以 JWT 作為 Session 權杖（需要 `jwt` extra）
- ✅ **滑動過期**，以及可選的絕對逾時
- ✅ **Flash 訊息**：在請求之間傳遞訊息
- ✅ **多種後端**：Redis、Memcached、記憶體
- ✅ **API 優先或以瀏覽器為主的架構**：用戶端可以自行保存權杖（標頭／Bearer），也可以交給瀏覽器以 Cookie 保存（`FastAPICacheXSessionMiddleware`）

## 快速開始 {#quick-start}

### 1. 安裝 {#1-installation}

Session 管理已內建於 FastAPI-CacheX：

```bash
uv add fastapi-cachex
```

若要啟用 JWT 權杖格式：

```bash
uv add "fastapi-cachex[jwt]"
```

### 2. 基本用法 {#2-basic-usage}

以下是 [`examples/session_api.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_api.py)（程式碼註解為英文）：API 用戶端登入後保存取得的權杖，並在之後的請求中送出它。

<!-- fmt:off -->
```python
--8<-- "examples/session_api.py"
```
<!-- fmt:on -->

範例也把管理器註冊到 `SessionManagerProxy`，0.4.0 起 `get_session_manager` 只會從那裡取得它（見[遷移至 0.4.0](MIGRATING_0_4.md#get-session-manager)）。管理器註冊在那裡之後，中介軟體也可以從 proxy 取得，而不必以參數傳入。省略 `config` 時，中介軟體會使用 `session_manager.config`：

```python
app.add_middleware(FastAPICacheXSessionMiddleware)  # 從 proxy 取得
```

當請求沒有帶著有效的 Session 時，`get_session`（及其別名 `require_session`）會拋出 `401 Authentication required`，並附上 `WWW-Authenticate: Bearer` 標頭。格式錯誤、偽造、已過期、已失效或未通過綁定檢查的權杖，在中介軟體層級都不會被視為錯誤：請求只會在沒有 Session 的情況下繼續處理。

依賴項回傳的 Session 物件是後端的 `Session` 模型。由 `FastAPICacheXSessionMiddleware` 從 `request.session` 建立的 Session（見下方的遷移一節）是匿名的，因此 `session.user` 為 `None`。

`get_session` 也接受這種 Session，因此它只能證明請求帶著「某個」Session，而不能證明有人登入。任何訪客只要進入會寫入 `request.session` 的路由（購物車、CSRF 值），就會得到一個。需要已登入使用者的路由，請改用 `require_user_session`（或其型別註記形式 `AuthenticatedSession`）保護，它在 `session.user` 為 `None` 時同樣回應 `401`，上方的 `/profile` 就是這樣做的。`/logout` 只會刪除 Session，因此使用 `SessionDep`（`get_session` 的型別註記形式）就足夠；`/public` 則使用 `OptionalSession`（`get_optional_session`），沒有 Session 時得到 `None`，而不是回應 `401`。

`UserSessionDep` 與 `AuthenticatedSession` 相同：匿名 Session 會得到 `401`。0.4.0 之前它是 `SessionDep` 的別名，也接受匿名 Session；路由確實需要接受匿名 Session 時，請使用 `SessionDep`（見[遷移至 0.4.0](MIGRATING_0_4.md#user-session-dep)）。

在 `FastAPICacheXSessionMiddleware` 底下，請以 `await login(request, user)` 讓使用者登入。它會以新的 Session ID 附加 `require_user_session`／`AuthenticatedSession` 檢查的 `SessionUser`，並由中介軟體送出權杖；見[登入後重新產生 Session ID](#5-regenerate-the-session-id-after-login)。上面的 `/login` 則是在本文中把權杖交給 API 用戶端：`create_session(user=...)` 同樣會設定 `session.user`，但中介軟體不會為不是由它載入或建立的 Session 送出任何東西。寫入 `request.session` 的鍵（`request.session["user_id"] = ...`）是應用程式資料：函式庫不會把它視為登入，因此這種 Session 仍會讓 `AuthenticatedSession` 回應 `401`。`session.user` 本身是唯讀的：指定它會拋出 `AttributeError`，因此除了建立時就帶有使用者的 `Session` 之外，Session 只能從 `login()` 或 `create_session(user=...)` 得到使用者。登出請使用 `await logout(request)`。

### 3. 完整範例（Redis 後端） {#3-full-example-redis-backend}

以下是 [`examples/session_redis.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_redis.py)（程式碼註解為英文）。它與 [`examples/redis_backend.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/redis_backend.py) 一樣，從 `REDIS_HOST`、`REDIS_PORT`、`REDIS_DB` 與 `REDIS_PASSWORD` 讀取 Redis 設定。

<!-- fmt:off -->
```python
--8<-- "examples/session_redis.py"
```
<!-- fmt:on -->

在 handler 中對 `Session` 物件所做的變更（Flash 訊息、`session.data`），只有在呼叫 `session_manager.update_session(session)` 時才會被儲存。這個儲存是有條件的：只有在後端仍存放這個物件最後一次讀到或寫入的內容時才會寫入，否則回傳 `False`。期間被另一個請求刪除、使其失效或輪替的 Session 不會被復活；兩個請求同時修改同一個 Session 時，先儲存的成功（見 [Session 寫入](MIGRATING_0_4.md#session-writes)）。

`delete_user_sessions()` 與 `clear_expired_sessions()` 會透過 `get_all_keys()` 列舉後端中的每一個鍵，並載入 `backend_key_prefix` 底下的每個 Session，因此其成本會隨後端的大小增加。在無法列舉鍵的 Memcached 後端上，它們找不到任何東西並回傳 `0`（後端會發出 `RuntimeWarning`）。

`clear_expired_sessions()` 會移除所有已無法使用的 Session：超過 `expires_at` 的，以及不再是 `ACTIVE` 的（以 `invalidate_session()` 作廢，或先前讀取時已標記為過期），否則它們會留在後端直到 TTL 到期。兩個方法都以單一次 `backend.delete_many()` 呼叫刪除找到的 Session。

## 遷移：SessionMiddleware → FastAPICacheXSessionMiddleware {#migration-sessionmiddleware-fastapicachexsessionmiddleware}

`SessionMiddleware` 自 0.3.1 起已棄用，已於 0.4.0 移除。請改用 `FastAPICacheXSessionMiddleware`：

- **`SessionMiddleware`**（一個 `BaseHTTPMiddleware`，已移除）：以自訂標頭（預設 `X-Session-Token`）和／或 `Authorization: Bearer` 傳遞權杖，適合由用戶端管理權杖的 API 優先架構。不支援以 Cookie 傳輸。
- **`FastAPICacheXSessionMiddleware`**（一個純 ASGI 中介軟體）：與 Starlette 內建的 `SessionMiddleware` 相容，提供相同的類 dict `request.session`。它以 Cookie（預設 Cookie 名稱 `session`）傳遞簽署過的 Session 權杖，而 Session 內容則存放在後端（`SessionManager` 的快取後端），而不是像 Starlette 自己的實作那樣編碼進 Cookie 本身。權杖解析採「標頭優先、Cookie 其次」：它會先讀取自訂標頭（預設 `X-Session-Token`）和／或 `Authorization: Bearer`，只有兩者都不存在時才退回使用 Cookie，因此原本搭配 `SessionMiddleware` 使用 `X-Session-Token` 的用戶端不需修改即可繼續運作。回應端同樣依來源決定：請求送出標頭或 Bearer 權杖時（即使該權杖已無法解析），新的或更新後的權杖會在 `header_name` 回應標頭中傳回，且不會發出 `Set-Cookie`；從 Cookie 傳入的權杖（或沒有權杖的請求所建立的全新匿名 Session）則使用 `Set-Cookie`。

`FastAPICacheXSessionMiddleware` 和 `SessionMiddleware` 一樣會將載入的 `Session` 物件放進 `request.state`，因此 Session 依賴項 `get_session`、`get_optional_session`、`require_session` 與 `require_user_session` 都能直接運作，不需任何修改：

```python
from fastapi import Depends
from fastapi_cachex.session import FastAPICacheXSessionMiddleware, require_user_session

app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=manager, config=config
)


@app.get("/me")
async def me(session=Depends(require_user_session)):
    return {"user_id": session.user.user_id}
```

### 搭配 `FastAPICacheXSessionMiddleware` 使用 `request.session` {#requestsession-with-fastapicachexsessionmiddleware}

`request.session` 是後端 Session 的 `data` dict 的一個視圖：

- 在沒有載入任何 Session 時寫入 `request.session`，會建立一個新的**匿名** Session（`SessionManager.create_anonymous_session()`，並依設定套用 IP / User-Agent 綁定），並透過該請求的傳輸方式傳回其權杖。
- 修改已載入 Session 的 `request.session`，會透過 `update_session()` 將新內容儲存到後端，以 dict 的內容取代 `Session.data`。若這個請求執行期間，另一個請求刪除、使其失效或輪替了這個 Session，或先一步儲存了它，這次儲存會被捨棄並記錄 log。回應不附 Session 權杖，除非這個請求已儲存了續期，且 Session 仍然有效，此時會送出續期後的權杖。
- 在已載入的 Session 上清除它（`request.session.clear()`）即為登出：即使資料原本就是空的，也會刪除後端的 Session；Cookie 用戶端還會收到一個使 Cookie 過期的 `Set-Cookie`。同一個請求中在 `clear()` 之後寫入的鍵，會存進一個使用新 ID 的新匿名 Session。`await logout(request)` 的效果相同，但會立即刪除後端的 Session，而不是等到送出回應時，且在該請求剩下的處理中，`get_session` 找不到 Session。
- 以 `del` 或 `pop()` 移除最後一個鍵並不是登出。帶有使用者的 Session 會以空資料儲存；匿名 Session 已無任何內容，會和 `clear()` 一樣被刪除。
- 以寫入 `request.session` 的方式登入時，會沿用請求帶來的 Session ID。Starlette 的中介軟體中 Cookie *就是* Session，因此登入回應會取代任何被植入的 Cookie；這裡的 Cookie 只是指向伺服器端紀錄的名稱，被植入的 Cookie 會跟著受害者一起登入。請以 `await login(request, user)` 登入，它會為 Session 換一個新 ID 並附加使用者（見[登入後重新產生 Session ID](#5-regenerate-the-session-id-after-login)）。
- 只要存取 `request.session`，或透過 Session 依賴項（`get_session`、`get_optional_session`，以及建立在它們之上的依賴項，例如 `AuthenticatedSession`）讀取 Session，就會為了尋找權杖而讀取過的每個請求標頭加入 `Vary`：依 `token_source_priority` 順序檢查的標頭（`header_name`，以及啟用 Bearer 權杖時的 `Authorization`），直到攜帶權杖的那一個為止。只有在沒有任何標頭攜帶權杖時才會讀取 Cookie，因此也只有這時才會加入 `Cookie`。
- 帶有 Session 權杖的回應（新建立的 Session、滑動續期、重新產生的 ID），或帶有讓 Session Cookie 失效之 `Set-Cookie` 的回應，一律不可快取。中介軟體會設定 `Cache-Control: private, no-store`，取代路由原本設定的值（包括 `@cache(public=True)` 的路由），並且即使處理函式沒有碰過 `request.session`，也會加入與上一項相同的 `Vary` 名稱。否則 CDN 或反向 proxy 可能存下權杖，再交給下一位訪客。不帶權杖的回應則維持原本的標頭。
- 對帶有 Session 的請求（中介軟體從任何來源載入的 Session，有沒有使用者都算，或不是空的 `request.session`），`@cache` 不會讀寫後端，並像 `Authorization` 一樣以 `private` 回應。`public=True` 讓路由在各 Session 間共用；`cache_authorized=True` 搭配包含 Session 使用者的 `key_builder` 則依使用者快取，回應仍帶有 `private`。見[需驗證身分的端點](HTTP_CACHING.md#authenticated-endpoints)。

Cookie 一律為 `HttpOnly`；`Secure`、`SameSite`、`Domain`、`Path` 與 `Max-Age` 則依 `cookie_*` 設定（`cookie_max_age=None` 或 `0` 時不設 `Max-Age`）。

## 設定 {#configuration}

### SessionConfig {#sessionconfig}

`SessionConfig` 是會拒絕未知欄位（`extra="forbid"`）的 Pydantic 模型，因此欄位名稱打錯字會拋出 `ValidationError`。下列值皆為預設值，唯獨 `secret_key` 是必填欄位。

```python
SessionConfig(
    # Session 存活時間
    session_ttl=3600,  # Session TTL（秒）
    absolute_timeout=None,  # 從 created_at 起算的硬性上限（秒）；None = 無上限
    sliding_expiration=True,  # 滑動過期
    sliding_threshold=0.5,  # 0.0-1.0；剩餘時間少於 TTL 的這個比例時更新
    # 權杖來源（API 優先架構）
    token_format="simple",  # "simple"（預設）或 "jwt"
    header_name="X-Session-Token",
    use_bearer_token=True,  # 已棄用：改為不在 token_source_priority 中列出 "bearer"
    token_source_priority=["header", "bearer"],  # "cookie" 只能放在最後（見下文）
    # JWT（token_format == "jwt" 時使用）
    jwt_algorithm="HS256",  # 拒絕 "none"
    jwt_issuer=None,  # 若有設定，會寫入 iss 並在解析時驗證
    jwt_audience=None,  # 若有設定，會寫入 aud 並在解析時驗證
    jwt_leeway=0,  # exp/iat 檢查的容許誤差秒數（nbf 既不發行也不驗證）
    # 安全性
    secret_key="...",  # 必填：至少 32 個字元
    ip_binding=False,  # IP 綁定
    user_agent_binding=False,  # User-Agent 綁定
    trusted_proxies=[],  # 受信任的反向 proxy 位址（見「用戶端 IP 與反向 proxy」）
    # 後端
    backend_key_prefix="session:",
    # Cookie（只有 FastAPICacheXSessionMiddleware 會讀取）
    cookie_name="__Host-session",  # 見下方「Cookie 預設值」
    cookie_max_age=14
    * 24
    * 60
    * 60,  # None = 不設 Max-Age（Cookie 隨瀏覽器工作階段結束）
    cookie_path="/",
    cookie_same_site="lax",  # "lax" / "strict" / "none"（"none" 需要 cookie_https_only=True）
    cookie_https_only=True,  # Secure 旗標：Cookie 只透過 HTTPS 傳送
    cookie_domain=None,  # None = 不設 Domain 屬性
)
```

#### Cookie 預設值 {#cookie-defaults}

Session Cookie 預設命名為 `__Host-session`，並帶有 `Secure` 旗標。瀏覽器只接受帶 `Secure`、`Path=/` 且沒有 `Domain` 的 `__Host-` Cookie，也不接受子網域設定的這種 Cookie，因此移除了植入 Session Cookie 最常見的途徑（Session 固定攻擊（session fixation））。

`Secure` Cookie 不會透過純 HTTP 傳送。沒有 TLS 的本機開發環境，請使用不帶前綴的名稱並關閉此旗標：`cookie_name="session", cookie_https_only=False`。

對瀏覽器會拒絕的 Cookie，`SessionConfig` 會引發 `ValidationError`：`__Host-` 名稱搭配 `cookie_https_only=False`、`/` 以外的 `cookie_path` 或 `cookie_domain`，以及 `__Secure-` 名稱未搭配 `cookie_https_only=True`。預設名稱帶有 `__Host-` 前綴，因此只變更其中一項設定也會引發錯誤；請一併變更 `cookie_name`。從 0.3.x 升級會變更 Cookie 名稱，使所有 Cookie Session 被登出一次；請參閱[遷移至 0.4.0](MIGRATING_0_4.md#session-cookie)。

Session 會在 `session_ttl` 秒後過期。啟用 `sliding_expiration` 時，每個發現剩餘時間少於 `session_ttl * sliding_threshold` 秒的請求，都會將過期時間重新延長為完整的 `session_ttl`，並發行一個更新後的權杖，由中介軟體傳回給用戶端（回應標頭或 `Set-Cookie`，見上表）。標頭／Bearer 用戶端在回應帶有 `header_name` 標頭時，應以它取代已保存的權杖。`absolute_timeout` 會在 Session 建立後經過該秒數時結束 Session，不論是否有滑動更新：過期時間、後端 TTL 與 JWT 的 `exp` 都不會超過 `created_at + absolute_timeout`，過期時間到達這個上限後也不再發行更新後的權杖。

#### `token_source_priority` 與 Session Cookie {#token_source_priority-and-the-session-cookie}

`FastAPICacheXSessionMiddleware` 先依照 `token_source_priority` 的順序讀取標頭來源，只有它們都沒有產生權杖時才退回使用 Cookie。回應端依權杖的來源決定（標頭進、標頭出；Cookie 進、`Set-Cookie` 出）。

在 0.4.0 以前，不論清單是否列出 Cookie，都會讀取 Cookie。`"cookie"` 只能放在清單的最後一項，也就是它現在本來就被讀取的位置，因此列出它目前不會改變任何行為；放在其他位置會引發 `ValidationError`。

0.4.0 起，這個清單列出所有權杖來源，預設值改為 `["header", "bearer", "cookie"]`，也就是目前使用的順序。沒有 `"cookie"` 的清單代表完全不使用 Cookie：中介軟體既不讀取也不設定它，而為沒有權杖的請求建立的 Session 會在 `header_name` 回應標頭中送出權杖（[#75](https://github.com/allen0099/FastAPI-CacheX/issues/75)）。因此，明確設定了不含 `"cookie"` 的清單時，`FastAPICacheXSessionMiddleware` 會發出 `FutureWarning`。要保留 Cookie，請把 `"cookie"` 加在最後一項；預設清單不會發出警告。見[遷移至 0.4.0](MIGRATING_0_4.md#token-source-priority)。

`use_bearer_token` 已棄用，並於 0.4.0 移除（[#377](https://github.com/allen0099/FastAPI-CacheX/issues/377)）：傳入它會發出 `DeprecationWarning`。請以不在清單中列出 `"bearer"`（`token_source_priority=["header", "cookie"]`）取代 `use_bearer_token=False`；`use_bearer_token=True` 是預設值，直接拿掉即可。

**標頭／Bearer 用戶端**應將權杖存放在 `localStorage` 或 `sessionStorage`，並以 `Authorization: Bearer <token>` 或 `X-Session-Token: <token>` 送出。**Cookie 用戶端**（瀏覽器）不需要自行處理權杖，但要留意 CSRF：瀏覽器會自動附上 Cookie，因此請將 `cookie_same_site` 與你自己的 CSRF 防護搭配使用。

### 使用 JWT 權杖格式 {#using-the-jwt-token-format}

設定 `token_format="jwt"` 時，Session 權杖會以帶有下列 claim 的 JWT 發行：

- `sid`：Session ID（自訂 claim，對應到伺服器端的 Session）
- `iat`：發行時間（epoch 秒數）
- `exp`：過期時間——Session 目前的 `expires_at`（因此會隨滑動更新移動），若無則退回 `iat + session_ttl`
- `iss`/`aud`：有設定時寫入，並在解析時驗證

設定範例：

```python
config = SessionConfig(
    secret_key="your-secret-key-at-least-32-characters",
    token_format="jwt",
    jwt_algorithm="HS256",
    jwt_issuer="your-issuer",
    jwt_audience="your-audience",
)
```

`jwt_algorithm` 必須是 `HS256`、`HS384`、`HS512`、`RS256`、`RS384`、`RS512`、`ES256`、`ES384`、`ES512`、`PS256`、`PS384`、`PS512` 或 `EdDSA` 其中之一；其他任何值（包括 `none`）都會拋出 `ValidationError`。內建的序列化器以同一把 `secret_key` 簽署與驗證，因此只支援 `HS256`、`HS384` 與 `HS512`：使用非對稱演算法時，除非你傳入持有金鑰對的自訂 `token_serializer`，否則 `SessionManager` 會拋出 `ValueError`。

HMAC 金鑰的長度至少須等於雜湊輸出（RFC 7518 §3.2）：`HS256` 為 32 位元組、`HS384` 為 48、`HS512` 為 64，以 UTF-8 編碼後計算。`secret_key` 只要求 32 個字元，因此搭配 `HS384` 或 `HS512` 時，較短的金鑰會讓 `SessionManager` 在建立內建序列化器時拋出 `ValueError`（自訂的 `token_serializer` 自行持有金鑰）。請使用更長的金鑰，例如 `secrets.token_urlsafe(64)`，或改用 `HS256`。

安全性注意事項：

- 伺服器保存的是**有狀態**的 Session（JWT 只是帶著 `sid` 的憑證），因此權杖中不需要放入任何敏感資料
- 解析時會驗證簽章與必要的 claim（`sid`/`iat`/`exp`，有設定時還包括 `iss`/`aud`）
- 正式環境請使用 HTTPS 與金鑰輪替策略（使用 `kid` 與多把金鑰的進階方案是未來可能的擴充）

**進階主題**：關於 JWT claim 的設計、為何未實作 `jti`/`nbf` 等可選 claim，以及如何加入自訂 claim，請參閱 **[JWT Claims 實作說明與擴充指南](JWT_CLAIMS.md)**。

## 安全性最佳實務 {#security-best-practices}

### 1. 密鑰 {#1-secret-key}

```python
import secrets

# 產生安全的密鑰
secret_key = secrets.token_urlsafe(32)

config = SessionConfig(secret_key=secret_key)
```

`secret_key` 以 `SecretStr` 儲存，且長度至少須為 32 個字元（`jwt_algorithm="HS384"` 時至少 48 位元組，`"HS512"` 時至少 64；見上方的 JWT 一節）。請從環境變數或密鑰儲存服務載入，而不要寫死在程式碼中；變更它會使至今發行的所有權杖失效。

### 2. 僅限 HTTPS {#2-https-only}

正式環境中一律透過 HTTPS 傳輸權杖。對 Cookie 用戶端，請保留 Cookie 的 `Secure` 旗標（預設即是如此）：

```python
config = SessionConfig(
    secret_key="...",
    cookie_name="__Host-session",  # 預設值；瀏覽器只接受帶 Secure、Path=/ 且沒有 Domain 的這種 Cookie
    cookie_https_only=True,  # 預設值；為 Session Cookie 加上 Secure 旗標
)
```

**用戶端注意事項**：

- 只透過 HTTPS 傳送權杖
- `FastAPICacheXSessionMiddleware` 設定的 Session Cookie 一律為 `HttpOnly`，因此頁面腳本無法讀取；存放在 `localStorage`/`sessionStorage` 的權杖可被腳本讀取，因此要防範 XSS
- 避免在 URL 中傳遞權杖

### 3. 用戶端 IP 與反向 proxy {#3-client-ip-and-reverse-proxies}

`ip_binding` 與稽核日誌所使用的「用戶端 IP」**預設只信任直接連線的對端位址**；`X-Forwarded-For` 與 `X-Real-IP` 會被忽略，因為任何人都可以送出這些標頭。

部署在反向 proxy 後方時，請將 proxy 的位址放進 `trusted_proxies`：

```python
config = SessionConfig(
    secret_key="...",
    ip_binding=True,
    trusted_proxies=["10.0.0.8"],  # 直接連線的那一跳
)
```

項目可以是單一位址或 CIDR 範圍，後者適用於從某個子網路連線的負載平衡器：

```python
config = SessionConfig(
    secret_key="...",
    ip_binding=True,
    trusted_proxies=["10.0.0.0/8", "2001:db8::/32"],
)
```

此時用戶端位址是 **`X-Forwarded-For` 中最右邊、且未列在 `trusted_proxies` 中的項目**：proxy 會附加到這個標頭後面，因此最左邊的項目是呼叫者自行選擇送出的內容，無法信任。當標頭分成多行送達時，它們會被當作一條以逗號分隔的鏈來讀取。若鏈中的每個項目都是受信任的 proxy，則使用直接連線的對端位址。`X-Real-IP` 由 proxy 自己寫入，沒有鏈可以走訪，因此只有在 `X-Forwarded-For` 無法產生可用的值時才會使用。

> [!NOTE]
> 以 IPv4 對應形式（`::ffff:10.0.0.8`，雙堆疊 socket 會這樣回報）回報的 IPv4 對端，會與 IPv4 項目比對相符。不是 IP 位址的項目（例如 TestClient 的 `testclient`）只會與完全相同的對端字串相符；而含有 `/` 但不是有效 CIDR 範圍的項目，會在建立設定時被拒絕。

中介軟體在檢查綁定時會套用這套邏輯，但 `create_session()` 綁定的是你傳入的任何 `ip_address`。在受信任的 proxy 後方，`request.client.host` 是 proxy 的位址，永遠不會相符，因此下一個請求的綁定檢查就會失敗。請改為傳入中介軟體推導出的位址，可以透過 `ClientIPDep` 依賴項，或以相同的設定呼叫 `get_client_ip()`：

```python
from fastapi_cachex.session import get_client_ip
from fastapi_cachex.session.dependencies import ClientIPDep, SessionManagerDep


@app.post("/login")
async def login(manager: SessionManagerDep, client_ip: ClientIPDep):
    session, token = await manager.create_session(user, ip_address=client_ip)
    return {"token": token}


# 在路由之外，使用你交給中介軟體的 SessionConfig：
client_ip = get_client_ip(request, config)
```

### 4. IP 綁定（可選） {#4-ip-binding-optional}

可提升安全性，但可能影響使用者體驗（例如用戶端的 IP 改變時）：

```python
config = SessionConfig(
    secret_key="...",
    ip_binding=True,  # 將 Session 綁定到用戶端 IP
)
```

綁定會在建立 Session 時記錄，取自傳給 `create_session()` 的 `ip_address`（`user_agent_binding` 則取自 `user_agent`）。若建立時缺少該值，會記錄一則警告，並建立未綁定的 Session。位址與綁定位址不符（或沒有位址）的請求，會被視為沒有 Session。

### 5. 登入後重新產生 Session ID {#5-regenerate-the-session-id-after-login}

防止 Session 固定攻擊（session fixation）。用戶端帶來的權杖可能是別人預先植入的（例如從同網域的其他子網域）；若登入時沿用它，植入者就會拿到一個已登入的 Session。在 `FastAPICacheXSessionMiddleware` 底下，`login()` 一次就會為 Session 換一個新 ID 並附加使用者：

```python
from fastapi import Request

from fastapi_cachex.session import SessionUser, login


# Credentials 是基本用法中的請求本文模型
@app.post("/login")
async def log_in(credentials: Credentials, request: Request):
    ...  # 驗證 credentials.password
    await login(request, SessionUser(user_id=credentials.username))
    return {"ok": True}
```

請求帶來的 Session 會如何處理，取決於它屬於誰：

- **匿名**（例如訪客的購物車）：以新 ID 保留其資料並得到使用者。
- **相同的 `user_id`**（重新登入）：同上，並以你傳入的 `SessionUser` 取代儲存的使用者，讓變更後的角色或 metadata 生效。
- **其他使用者的**：它會被刪除，連同該請求中先前寫入 `request.session` 的內容，`login()` 會建立新的 Session。前一位使用者的資料（購物車、`elevated` 旗標）都不會帶給新使用者。
- **沒有**（新訪客，或權杖無法解析）：`login()` 會建立帶有使用者的 Session，並依設定綁定用戶端 IP 與 User-Agent。

若只想帶入部分資料，請列出鍵：`login(request, user, keep=["cart"])` 會丟棄其他所有鍵，包括已載入 Session 中的鍵，以及該請求中先前寫入 `request.session` 的鍵；`keep=[]` 則全部丟棄。呼叫之後寫入的鍵會保留。`keep` 必須是鍵的集合，因此傳入字串會拋出 `TypeError`。

無論哪種情況，舊的權杖都無法再解析出 Session。接著中介軟體會儲存該 Session（包括呼叫之後寫入 `request.session` 的鍵；除非已載入的 Session 屬於其他使用者，也包括呼叫之前寫入的鍵），並透過該請求使用的傳輸方式送出權杖：以標頭或 `Authorization: Bearer` 權杖送來的請求使用回應標頭，否則使用帶有所有 `cookie_*` 屬性的 HttpOnly `Set-Cookie`。和每個帶有權杖的回應一樣，它會加上 `Cache-Control: private, no-store`。之後帶著該權杖的請求會通過 `require_user_session` 與 `AuthenticatedSession`。`login()` 會回傳該 Session，在該請求剩下的處理中，`get_session` 也會回傳它。

完全沒有帶權杖的請求只會收到 Cookie，頁面上的指令碼讀不到它。不要把權杖複製到瀏覽器登入回應的標頭或本文中。沒有權杖就登入的 API 用戶端需要從本文取得權杖：對 `login()` 回傳的 Session 回傳 `manager.issue_token(session)`，或由另一個端點發出權杖，如 [`examples/session_jwt.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt.py) 所示。完整的瀏覽器版本請見 [`examples/session_login.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_login.py)。

登出請呼叫 `await logout(request)`。它會立即從後端刪除 Session，因此權杖在回應送出之前就已失效，Cookie 用戶端也會收到讓 Cookie 過期的回應。它回傳 `True`；若該請求中沒有載入或建立任何 Session（包括權杖無法解析的情況），則回傳 `False`。之後寫入 `request.session` 的鍵會存進新的匿名 Session，之後呼叫的 `login()` 則會建立新的 Session。

在同一個請求中，`login()` 之後呼叫 `request.session.clear()` 就是登出：新的 Session 會被刪除，也不會送出權杖（Cookie 用戶端的 Cookie 會被設為過期）。在 `login()` 之前呼叫 `clear()` 會讓已載入的 Session 登出，`login()` 接著會建立新的 Session，而不是為它換 ID。沒有 `FastAPICacheXSessionMiddleware` 時，`login()` 與 `logout()` 會拋出 `RuntimeError`，因為沒有人會送出權杖或讓 Cookie 過期；請改以 `create_session(user=...)` 建立 Session 並回傳其權杖，並以 `delete_session()` 結束它。

`request.session["user_id"] = "123"` 不是登入。它是應用程式資料，`require_user_session` 與 `AuthenticatedSession` 不會認得它，而且它會沿用請求帶來的 Session ID。

若要在不登入的情況下更換 ID（例如權限變更之後），請呼叫 `await rotate_session_id(request)`：

```python
from fastapi_cachex.session import rotate_session_id
from fastapi_cachex.session.dependencies import AuthenticatedSession


@app.post("/sudo")
async def sudo(request: Request, session: AuthenticatedSession):
    ...  # 再次檢查密碼
    await rotate_session_id(request)
    request.session["elevated"] = True
    return {"ok": True}
```

`rotate_session_id()` 會對請求的 Session 呼叫 `SessionManager.regenerate_session_id()`，刪除舊 ID 底下的後端紀錄，並以新 ID 儲存該 Session，保留其資料、使用者、`created_at` 與過期時間。任一個中介軟體都會看到新 ID，並透過該請求使用的傳輸方式送出對應的權杖：Cookie 使用 `Set-Cookie`，標頭權杖則使用回應標頭。之後舊的權杖就無法再解析出 Session。新訪客沒有可換 ID 的 Session，因此它會回傳 `False`。若這個請求執行期間，另一個請求刪除、使其失效或輪替了這個 Session，則不會儲存或送出任何東西，並回應 `401`；`login()` 則改為替使用者建立新的 Session（見 [Session 寫入](MIGRATING_0_4.md#session-writes)）。

已經取得請求 Session 物件的 handler，也可以直接呼叫 `await manager.regenerate_session_id(session)`。請從 `get_optional_session` 取得 Session，並在它為 `None` 時略過呼叫；`SessionDep` 會對還沒有 Session 的訪客回應 `401`。與 `rotate_session_id()` 不同，若期間另一個請求結束了這個 Session，直接呼叫會拋出 `SessionNotFoundError` 或 `SessionInvalidError`，因此請捕捉 `SessionError`，並比照沒有 Session 的情況回應。

在中介軟體之外，請以中介軟體會傳入的相同綁定值載入 Session，並自行將回傳的權杖交給用戶端：

```python
session, _ = await manager.get_session(
    current_token, ip_address=client_ip, user_agent=user_agent
)
session, new_token = await manager.regenerate_session_id(session)
```

## SessionManager 概覽 {#sessionmanager-at-a-glance}

`SessionManager(backend, config, token_serializer=None)` 處理整個生命週期：`create_session()`／`create_anonymous_session()` 回傳 `(session, token)`；`get_session()` 回傳 `(session, renewed_token)`，其中 `renewed_token` 只有在滑動過期更新了權杖時才會有值，並應傳回給用戶端。`get_session()` 失敗時會拋出 `SessionError` 的子類別：`SessionTokenError`（權杖格式錯誤；JWT 還包括簽章錯誤、`exp` 已過期或 `iss`／`aud` 不符）、`SessionSecurityError`（`simple` 權杖簽章錯誤或綁定不符）、`SessionNotFoundError`、`SessionInvalidError`（Session 不是啟用狀態）或 `SessionExpiredError`（超過 TTL 或絕對逾時）。從 0.3.8 起，`SessionError` 繼承自 `CacheXError`，因此 `except CacheXError` 也會捕捉 Session 錯誤。

`get_session()` 只有在滑動過期更新了 Session 時才寫入後端，因此只讀取 Session 的請求只需一次後端讀取。回傳的 Session 中 `last_accessed` 是目前時間，但儲存的值只會在 Session 下一次被寫入（建立、修改、更新或重新產生）時更新。傳入 `touch=True` 可在每次查詢時都儲存它。0.3.8 之前，每次查詢都會儲存 Session。

每個方法及其簽名請見自動產生的 [Session API 參考](https://fastapi-cachex.readthedocs.io/en/latest/api/session/)（英文）。

## 依賴項 {#dependencies}

```python
from fastapi_cachex.session import (
    get_session,  # 需要驗證（沒有 Session 時回應 401）
    get_optional_session,  # 可選驗證（沒有 Session 時為 None）
    require_session,  # get_session 的別名
    require_user_session,  # Session 沒有使用者時也回應 401
    get_session_manager,  # 中介軟體註冊的 SessionManager
    login,  # 不是依賴項：await 它以新的 Session ID 讓使用者登入
    rotate_session_id,  # 不是依賴項：await 它以取得新的 Session ID
)

# 型別註記
from fastapi_cachex.session.dependencies import (
    OptionalSession,  # Session | None
    RequiredSession,  # Session
    SessionDep,  # Session
    UserSessionDep,  # 自 0.4.0 起與 AuthenticatedSession 相同
    AuthenticatedSession,  # 帶有使用者的 Session（require_user_session）
    SessionManagerDep,  # SessionManager
)
```

`get_session_manager` 回傳中介軟體在處理第一個請求時存放在 `app.state` 上的管理器；若尚未有任何 Session 中介軟體執行過，它會回應 `500`。使用它可以避免在路由模組中匯入管理器。0.4.0 起它改為透過 `SessionManagerProxy` 取得管理器，因此請以 `SessionManagerProxy.set(manager)` 註冊：在那之前，當 proxy 沒有管理器或持有不同的管理器時，`get_session_manager`（以及使用它的 `SessionManagerDep`、`ClientIPDep` 與 `rotate_session_id()`）每個應用程式會發出一次 `FutureWarning`。請參閱[遷移至 0.4.0](MIGRATING_0_4.md#get-session-manager)。

```python
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session.dependencies import SessionManagerDep


# Credentials 是基本用法中的請求本文模型
@app.post("/login")
async def login(credentials: Credentials, manager: SessionManagerDep):
    ...  # 驗證 credentials.password
    user = SessionUser(user_id=credentials.username)
    session, token = await manager.create_session(user=user)
    return {"token": token}
```

`get_session` 與 `get_optional_session` 也會宣告一個 `HTTPBearer` 安全性方案（`SessionBearer`），因此 Swagger UI 會顯示 **Authorize** 按鈕；權杖本身仍由中介軟體讀取。
