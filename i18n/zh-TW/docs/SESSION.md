# Session 管理擴充 {#session-management-extension}

FastAPI-CacheX 的 Session 管理提供完整的使用者 Session 處理，包括簽署過的權杖、滑動過期，以及可選的 IP / User-Agent 綁定。Session 內容一律存放在快取後端；用戶端只持有一個簽署過的權杖。

**權杖如何傳遞，取決於你安裝的是哪一個中介軟體：**

| 中介軟體 | 權杖來源 | 回應端 | 狀態 |
|------------|--------------|---------------|--------|
| `FastAPICacheXSessionMiddleware` | 自訂標頭（預設 `X-Session-Token`）／`Authorization: Bearer`／**Cookie**（預設名稱 `session`） | 依來源決定：從標頭傳入的權杖會在回應標頭中傳回；從 Cookie 傳入的權杖（或全新的 Session）則使用 `Set-Cookie` | **建議使用** |
| `SessionMiddleware` | 自訂標頭／`Authorization: Bearer`；**不支援 Cookie** | 更新後的權杖會在回應標頭中傳回 | 已棄用，**將於 0.4.0 移除** |

**所有新專案請使用 `FastAPICacheXSessionMiddleware`。** 它涵蓋 `SessionMiddleware` 的所有傳輸方式（以相同方式讀取 `X-Session-Token` 與 `Authorization: Bearer`），並加入 Cookie 支援。自 0.3.1 起，`SessionMiddleware` 在建構時會發出 `DeprecationWarning`，並將於 **0.4.0 移除**。兩個中介軟體提供給相同的 Session 依賴項（`get_session`、`get_optional_session`、`require_session`），因此遷移通常只需要修改 `add_middleware` 那一行；以標頭傳送權杖的既有用戶端不需要任何修改。

`SessionConfig` 的六個 `cookie_*` 設定（`cookie_name`、`cookie_max_age`、`cookie_path`、`cookie_same_site`、`cookie_https_only`、`cookie_domain`）**只有 `FastAPICacheXSessionMiddleware` 會讀取**；安裝的是 `SessionMiddleware` 時，設定它們不會有任何效果。

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

```python
from fastapi import Depends, FastAPI, HTTPException
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.session import (
    FastAPICacheXSessionMiddleware,
    SessionConfig,
    SessionManager,
    SessionUser,
    get_optional_session,
    get_session,
)

# 建立 FastAPI 應用程式
app = FastAPI()

# Session 設定（API 優先架構：由用戶端管理權杖）
config = SessionConfig(
    secret_key="your-secret-key-min-32-chars-long!!!",  # 至少 32 個字元
    session_ttl=3600,  # 1 小時
)

# 設定後端與 Session 管理器
backend = MemoryBackend()
session_manager = SessionManager(backend, config)

# 加入 Session 中介軟體（SessionMiddleware 已棄用，將於 0.4.0 移除）
app.add_middleware(
    FastAPICacheXSessionMiddleware,
    session_manager=session_manager,
    config=config,
)

# 或者，也可以將管理器註冊到 proxy，而不是直接傳入：
#
#     from fastapi_cachex.session import SessionManagerProxy
#
#     SessionManagerProxy.set(session_manager)
#     app.add_middleware(FastAPICacheXSessionMiddleware)  # 從 proxy 取得
#
# 省略 `config` 時，中介軟體會使用 `session_manager.config`。


# 登入端點
@app.post("/login")
async def login(username: str, password: str):
    # 驗證使用者（此處為簡化版）
    if username != "admin" or password != "secret":
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # 建立 Session
    user = SessionUser(
        user_id="123",
        username=username,
        roles=["admin"],
    )
    session, token = await session_manager.create_session(user=user)

    # 回傳權杖供用戶端保存（localStorage/sessionStorage）。
    # 用戶端之後的請求會在 Authorization 或 X-Session-Token 標頭中送出它。
    return {"message": "Login successful", "token": token}


# 需要驗證的端點
@app.get("/profile")
async def get_profile(session=Depends(get_session)):
    """Requires a valid session."""
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
        "roles": session.user.roles,
    }


# 可選驗證的端點
@app.get("/public")
async def public_endpoint(session=Depends(get_optional_session)):
    """Accessible with or without a session."""
    if session and session.user:
        return {"message": f"Hello, {session.user.username}!"}
    return {"message": "Hello, guest!"}


# 登出端點
@app.post("/logout")
async def logout(session=Depends(get_session)):
    await session_manager.delete_session(session.session_id)
    return {"message": "Logged out"}
```

當請求沒有帶著有效的 Session 時，`get_session`（及其別名 `require_session`）會拋出 `401 Authentication required`，並附上 `WWW-Authenticate: Bearer` 標頭。格式錯誤、偽造、已過期、已失效或未通過綁定檢查的權杖，在中介軟體層級都不會被視為錯誤：請求只會在沒有 Session 的情況下繼續處理。

依賴項回傳的 Session 物件是後端的 `Session` 模型。由 `FastAPICacheXSessionMiddleware` 從 `request.session` 建立的 Session（見下方的遷移一節）是匿名的，因此 `session.user` 為 `None`。

### 3. 完整範例（Redis 後端） {#3-full-example-redis-backend}

```python
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.session import (
    FastAPICacheXSessionMiddleware,
    SessionConfig,
    SessionManager,
    SessionUser,
    get_session,
)
from fastapi_cachex.session.dependencies import ClientIPDep

app = FastAPI()

# Redis 後端
backend = AsyncRedisCacheBackend(
    host="localhost",
    port=6379,
    db=0,
)

# 包含安全性選項的 Session 設定
config = SessionConfig(
    secret_key="your-very-secret-key-at-least-32-characters-long!!",
    session_ttl=3600,
    sliding_expiration=True,
    sliding_threshold=0.5,
    ip_binding=True,  # 啟用 IP 綁定
    user_agent_binding=False,  # UA 綁定（可選）
)

session_manager = SessionManager(backend, config)

app.add_middleware(
    FastAPICacheXSessionMiddleware,
    session_manager=session_manager,
    config=config,
)


@app.post("/api/auth/login")
async def login(username: str, password: str, request: Request, client_ip: ClientIPDep):
    # 驗證使用者（實際上應查詢資料庫）
    if not authenticate_user(username, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # 建立 Session
    user = SessionUser(
        user_id=get_user_id(username),
        username=username,
        email=f"{username}@example.com",
        roles=get_user_roles(username),
    )

    # 收集綁定所需的用戶端資訊。`client_ip` 就是中介軟體之後
    # 會檢查的位址，在受信任的 proxy 後方也是如此。
    user_agent = request.headers.get("user-agent")

    session, token = await session_manager.create_session(
        user=user,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    # 加入 Flash 訊息
    session.add_flash_message("Login successful!", "success")
    await session_manager.update_session(session)

    return {
        "message": "Login successful",
        "token": token,  # 用戶端保存這個權杖，並在之後的請求中送出
        "user": {
            "username": user.username,
            "roles": user.roles,
        },
    }


@app.get("/api/user/profile")
async def get_user_profile(session=Depends(get_session)):
    """Return the user's profile (requires authentication)."""
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
        "email": session.user.email,
        "roles": session.user.roles,
        "session_created": session.created_at.isoformat(),
        "last_accessed": session.last_accessed.isoformat(),
    }


@app.post("/api/user/update")
async def update_user_profile(
    email: str,
    session=Depends(get_session),
):
    """Update the user's profile."""
    session.user.email = email
    session.data["last_updated"] = datetime.now(timezone.utc).isoformat()

    # 儲存更新後的 Session
    await session_manager.update_session(session)

    return {"message": "Profile updated"}


@app.get("/api/messages")
async def get_flash_messages(session=Depends(get_session)):
    """Return and clear the flash messages."""
    messages = session.get_flash_messages(clear=True)
    # 清除只會改變記憶體中的物件；請儲存它，
    # 以免下一個請求再次顯示這些訊息。
    await session_manager.update_session(session)
    return {"messages": messages}


@app.post("/api/auth/logout")
async def logout(session=Depends(get_session)):
    """Log out."""
    await session_manager.delete_session(session.session_id)

    # 用戶端應丟棄已保存的權杖
    return {"message": "Logged out successfully"}


@app.post("/api/auth/logout-all")
async def logout_all_devices(session=Depends(get_session)):
    """Log out from all devices."""
    user_id = session.user.user_id
    count = await session_manager.delete_user_sessions(user_id)
    return {"message": f"Logged out from {count} devices"}


# 輔助函式（僅為示意）
def authenticate_user(username: str, password: str) -> bool:
    # 實際的實作會查詢資料庫並驗證密碼雜湊
    return True


def get_user_id(username: str) -> str:
    # 實際的實作會從資料庫讀取
    return f"user_{username}"


def get_user_roles(username: str) -> list[str]:
    # 實際的實作會從資料庫讀取
    return ["user"] if username != "admin" else ["admin", "user"]
```

在 handler 中對 `Session` 物件所做的變更（Flash 訊息、`session.data`、`session.user`），只有在呼叫 `session_manager.update_session(session)` 時才會被儲存。

`delete_user_sessions()` 與 `clear_expired_sessions()` 會透過 `get_all_keys()` 列舉後端中的每一個鍵，並載入 `backend_key_prefix` 底下的每個 Session，因此其成本會隨後端的大小增加。在無法列舉鍵的 Memcached 後端上，它們找不到任何東西並回傳 `0`（後端會發出 `RuntimeWarning`）。

## 遷移：SessionMiddleware → FastAPICacheXSessionMiddleware {#migration-sessionmiddleware-fastapicachexsessionmiddleware}

`SessionMiddleware` 自 0.3.1 起已棄用（建構時會發出 `DeprecationWarning`），並將於 0.4.0 移除。請改用 `FastAPICacheXSessionMiddleware`：

- **`SessionMiddleware`**（一個 `BaseHTTPMiddleware`）：以自訂標頭（預設 `X-Session-Token`）和／或 `Authorization: Bearer` 傳遞權杖，適合由用戶端管理權杖的 API 優先架構。不支援以 Cookie 傳輸。
- **`FastAPICacheXSessionMiddleware`**（一個純 ASGI 中介軟體）：與 Starlette 內建的 `SessionMiddleware` 相容，提供相同的類 dict `request.session`。它以 Cookie（預設 Cookie 名稱 `session`）傳遞簽署過的 Session 權杖，而 Session 內容則存放在後端（`SessionManager` 的快取後端），而不是像 Starlette 自己的實作那樣編碼進 Cookie 本身。權杖解析採「標頭優先、Cookie 其次」：它會先讀取自訂標頭（預設 `X-Session-Token`）和／或 `Authorization: Bearer`，只有兩者都不存在時才退回使用 Cookie，因此原本搭配 `SessionMiddleware` 使用 `X-Session-Token` 的用戶端不需修改即可繼續運作。回應端同樣依來源決定：從標頭傳入的權杖，更新後的權杖會在同一個回應標頭中傳回，且不會發出 `Set-Cookie`；從 Cookie 傳入的權杖（或全新的匿名 Session）則使用 `Set-Cookie`。

兩個中介軟體都會將載入的 `Session` 物件放進 `request.state`，因此既有的 Session 依賴項 `get_session`、`get_optional_session` 與 `require_session` 在任一個中介軟體下都能直接運作，不需任何修改：

```python
from fastapi import Depends
from fastapi_cachex.session import FastAPICacheXSessionMiddleware, get_session

app.add_middleware(
    FastAPICacheXSessionMiddleware, session_manager=manager, config=config
)


@app.get("/me")
async def me(session=Depends(get_session)):
    return {"user_id": session.user.user_id}
```

### 搭配 `FastAPICacheXSessionMiddleware` 使用 `request.session` {#requestsession-with-fastapicachexsessionmiddleware}

`request.session` 是後端 Session 的 `data` dict 的一個視圖：

- 在沒有載入任何 Session 時寫入 `request.session`，會建立一個新的**匿名** Session（`SessionManager.create_anonymous_session()`，並依設定套用 IP / User-Agent 綁定），並透過該請求的傳輸方式傳回其權杖。
- 修改已載入 Session 的 `request.session`，會透過 `update_session()` 將新內容儲存到後端，以 dict 的內容取代 `Session.data`。
- 在原本有資料的 Session 上清除它（`request.session.clear()`），會刪除後端的 Session；Cookie 用戶端還會收到一個使 Cookie 過期的 `Set-Cookie`。
- 只要存取 `request.session`，就會為了尋找權杖而讀取過的每個請求標頭加入 `Vary`：依 `token_source_priority` 順序檢查的標頭（`header_name`，以及啟用 Bearer 權杖時的 `Authorization`），直到攜帶權杖的那一個為止。只有在沒有任何標頭攜帶權杖時才會讀取 Cookie，因此也只有這時才會加入 `Cookie`。

Cookie 一律為 `HttpOnly`；`Secure`、`SameSite`、`Domain`、`Path` 與 `Max-Age` 則依 `cookie_*` 設定。

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
    use_bearer_token=True,
    token_source_priority=["header", "bearer"],  # 只接受這兩個值（見下文）
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
    cookie_name="session",
    cookie_max_age=14
    * 24
    * 60
    * 60,  # None = 不設 Max-Age（Cookie 隨瀏覽器工作階段結束）
    cookie_path="/",
    cookie_same_site="lax",  # "lax" / "strict" / "none"
    cookie_https_only=False,  # True 會加上 Secure 旗標
    cookie_domain=None,  # None = 不設 Domain 屬性
)
```

Session 會在 `session_ttl` 秒後過期。啟用 `sliding_expiration` 時，每個發現剩餘時間少於 `session_ttl * sliding_threshold` 秒的請求，都會將過期時間重新延長為完整的 `session_ttl`，並發行一個更新後的權杖，由中介軟體傳回給用戶端（回應標頭或 `Set-Cookie`，見上表）。標頭／Bearer 用戶端在回應帶有 `header_name` 標頭時，應以它取代已保存的權杖。`absolute_timeout` 會在 Session 建立後經過該秒數時結束 Session，不論是否有滑動更新：過期時間、後端 TTL 與 JWT 的 `exp` 都不會超過 `created_at + absolute_timeout`，過期時間到達這個上限後也不再發行更新後的權杖。

#### `token_source_priority` 只接受 `"header"` 與 `"bearer"` {#token_source_priority-accepts-only-header-and-bearer}

此欄位的型別為 `list[Literal["header", "bearer"]]`；傳入 `"cookie"` 會被 Pydantic 以 `ValidationError` 拒絕。Cookie **不**屬於優先順序的一部分：`FastAPICacheXSessionMiddleware` 以固定順序解析權杖——它先依照 `token_source_priority` 讀取標頭／Bearer，只有兩者都沒有產生權杖時才退回使用 Cookie。這是刻意的設計：回應端是依權杖的來源決定（標頭進、標頭出；Cookie 進、`Set-Cookie` 出），若將 Cookie 混入同一個優先順序清單，`["cookie"]` 這個設定在已棄用的 `SessionMiddleware` 上就會無聲地失效。等到 `SessionMiddleware` 於 0.4.0 移除後，三種來源或許可以用單一的優先順序清單描述。

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

`secret_key` 以 `SecretStr` 儲存，且長度至少須為 32 個字元。請從環境變數或密鑰儲存服務載入，而不要寫死在程式碼中；變更它會使至今發行的所有權杖失效。

### 2. 僅限 HTTPS {#2-https-only}

正式環境中一律透過 HTTPS 傳輸權杖。對 Cookie 用戶端，請將 Cookie 標記為 `Secure`：

```python
config = SessionConfig(
    secret_key="...",
    cookie_https_only=True,  # 為 Session Cookie 加上 Secure 旗標
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

防止 Session 固定攻擊（session fixation）：

```python
from fastapi_cachex.session.dependencies import SessionDep, SessionManagerDep


@app.post("/login")
async def login(request: Request, session: SessionDep, manager: SessionManagerDep):
    ...  # 驗證帳號密碼
    await manager.regenerate_session_id(session)
    request.session["user_id"] = "123"
    return {"ok": True}
```

`regenerate_session_id()` 會刪除舊 ID 底下的後端紀錄，並以新 ID 儲存該 Session，保留其資料、使用者、`created_at` 與過期時間。當該 Session 是請求本身的 Session（來自 `SessionDep`、`get_session` 等）時，任一個中介軟體都會看到新 ID，並透過該請求使用的傳輸方式送出對應的權杖：Cookie 使用 `Set-Cookie`，標頭權杖則使用回應標頭。之後舊的權杖就無法再解析出 Session。

在中介軟體之外，請以中介軟體會傳入的相同綁定值載入 Session，並自行將回傳的權杖交給用戶端：

```python
session, _ = await manager.get_session(
    current_token, ip_address=client_ip, user_agent=user_agent
)
session, new_token = await manager.regenerate_session_id(session)
```

## SessionManager 概覽 {#sessionmanager-at-a-glance}

`SessionManager(backend, config, token_serializer=None)` 處理整個生命週期：`create_session()`／`create_anonymous_session()` 回傳 `(session, token)`；`get_session()` 回傳 `(session, renewed_token)`，其中 `renewed_token` 只有在滑動過期更新了權杖時才會有值，並應傳回給用戶端。`get_session()` 失敗時會拋出 `SessionError` 的子類別：`SessionTokenError`（權杖格式錯誤）、`SessionSecurityError`（簽章錯誤或綁定不符）、`SessionNotFoundError`、`SessionInvalidError`（Session 不是啟用狀態）或 `SessionExpiredError`（超過 TTL 或絕對逾時）。

每個方法及其簽名請見自動產生的 [Session API 參考](https://fastapi-cachex.readthedocs.io/en/latest/api/session/)（英文）。

## 依賴項 {#dependencies}

```python
from fastapi_cachex.session import (
    get_session,  # 需要驗證（沒有 Session 時回應 401）
    get_optional_session,  # 可選驗證（沒有 Session 時為 None）
    require_session,  # get_session 的別名
    get_session_manager,  # 中介軟體註冊的 SessionManager
)

# 型別註記
from fastapi_cachex.session.dependencies import (
    OptionalSession,  # Session | None
    RequiredSession,  # Session
    SessionDep,  # Session
    SessionManagerDep,  # SessionManager
)
```

`get_session_manager` 回傳中介軟體在處理第一個請求時存放在 `app.state` 上的管理器；若尚未有任何 Session 中介軟體執行過，它會回應 `500`。使用它可以避免在路由模組中匯入管理器：

```python
from fastapi_cachex.session import SessionUser
from fastapi_cachex.session.dependencies import SessionManagerDep


@app.post("/login")
async def login(username: str, manager: SessionManagerDep):
    session, token = await manager.create_session(user=SessionUser(user_id=username))
    return {"token": token}
```

`get_session` 與 `get_optional_session` 也會宣告一個 `HTTPBearer` 安全性方案（`SessionBearer`），因此 Swagger UI 會顯示 **Authorize** 按鈕；權杖本身仍由中介軟體讀取。
