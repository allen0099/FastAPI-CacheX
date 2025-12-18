# Session Management Extension

FastAPI-CacheX Session Management 提供完整的使用者 Session 管理功能，包含安全的 token 簽名、Cookie/Header 處理、自動展期等特性。

## 特性

- ✅ **Session 生命週期管理**：建立、讀取、更新、刪除
- ✅ **安全機制**：
  - HMAC-SHA256 Token 簽名
  - IP 地址綁定（可選）
  - User-Agent 綁定（可選）
  - 登入後自動重新生成 Session ID
- ✅ **多種 Token 來源**：Header、Bearer Token
- ✅ **自動展期**：滑動過期時間支援
- ✅ **Flash Messages**：跨請求訊息傳遞
- ✅ **多後端支援**：Redis、Memcached、In-Memory
- ✅ **API-first 架構**：適用於前後端分離應用，由客戶端管理 Token

## 快速開始

### 1. 安裝

Session 管理已整合在 FastAPI-CacheX 中：

```bash
uv add fastapi-cachex
```

### 2. 基本使用

```python
from fastapi import FastAPI
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.session import (
    SessionManager,
    SessionMiddleware,
    SessionConfig,
    SessionUser,
    get_session,
    get_optional_session,
)

# 初始化 FastAPI 應用
app = FastAPI()

# 設定 Session 配置（API-first 架構：客戶端管理 Token）
config = SessionConfig(
    secret_key="your-secret-key-min-32-chars-long!!!",
    session_ttl=3600,  # 1 hour
)

# 設定後端和 Session Manager
backend = MemoryBackend()
session_manager = SessionManager(backend, config)

# 添加 Session Middleware
app.add_middleware(
    SessionMiddleware,
    session_manager=session_manager,
    config=config,
)


# 登入端點
@app.post("/login")
async def login(username: str, password: str):
    # 驗證使用者（這裡簡化處理）
    if username == "admin" and password == "secret":
        # 建立 Session
        user = SessionUser(
            user_id="123",
            username=username,
            roles=["admin"],
        )
        session, token = await session_manager.create_session(user=user)

        # 返回 token 供客戶端儲存（localStorage/sessionStorage）
        # 客戶端應在後續請求中透過 Authorization header 或 X-Session-Token header 傳送
        return {"message": "Login successful", "token": token}

    return {"error": "Invalid credentials"}, 401


# 需要認證的端點
@app.get("/profile")
async def get_profile(session=Depends(get_session)):
    """需要有效 session 才能存取"""
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
        "roles": session.user.roles,
    }


# 可選認證的端點
@app.get("/public")
async def public_endpoint(session=Depends(get_optional_session)):
    """可以有或沒有 session 存取"""
    if session:
        return {"message": f"Hello, {session.user.username}!"}
    return {"message": "Hello, guest!"}


# 登出端點
@app.post("/logout")
async def logout(session=Depends(get_session)):
    await session_manager.delete_session(session.session_id)
    return {"message": "Logged out"}
```

### 3. 完整範例（含 Redis 後端）

```python
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.session import (
    SessionManager,
    SessionMiddleware,
    SessionConfig,
    SessionUser,
    get_session,
    get_optional_session,
)

app = FastAPI()

# Redis 後端配置
backend = AsyncRedisCacheBackend(
    host="localhost",
    port=6379,
    db=0,
)

# Session 配置（含安全選項）
config = SessionConfig(
    secret_key="your-very-secret-key-at-least-32-characters-long!!",
    session_ttl=3600,
    sliding_expiration=True,
    sliding_threshold=0.5,
    ip_binding=True,  # 啟用 IP 綁定
    user_agent_binding=False,  # UA 綁定（可選）
    regenerate_on_login=True,
)

session_manager = SessionManager(backend, config)

app.add_middleware(
    SessionMiddleware,
    session_manager=session_manager,
    config=config,
)


@app.post("/api/auth/login")
async def login(username: str, password: str, request: Request):
    # 驗證使用者（應該查詢資料庫）
    if not authenticate_user(username, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # 建立 Session
    user = SessionUser(
        user_id=get_user_id(username),
        username=username,
        email=f"{username}@example.com",
        roles=get_user_roles(username),
    )

    # 獲取客戶端資訊
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    session, token = await session_manager.create_session(
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # 添加 flash message
    session.add_flash_message("Login successful!", "success")
    await session_manager.update_session(session)

    return {
        "message": "Login successful",
        "token": token,  # 客戶端應儲存此 token 並在後續請求中使用
        "user": {
            "username": user.username,
            "roles": user.roles,
        },
    }


@app.get("/api/user/profile")
async def get_user_profile(session=Depends(get_session)):
    """獲取使用者資料（需要認證）"""
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
    """更新使用者資料"""
    # 更新使用者資料
    session.user.email = email
    session.data["last_updated"] = datetime.now(timezone.utc).isoformat()

    # 儲存更新後的 session
    await session_manager.update_session(session)

    return {"message": "Profile updated"}


@app.get("/api/messages")
async def get_flash_messages(session=Depends(get_session)):
    """獲取 flash messages"""
    messages = session.get_flash_messages(clear=True)
    return {"messages": messages}


@app.post("/api/auth/logout")
async def logout(session=Depends(get_session)):
    """登出"""
    await session_manager.delete_session(session.session_id)

    # 客戶端應該清除儲存的 token
    return {"message": "Logged out successfully"}


@app.post("/api/auth/logout-all")
async def logout_all_devices(session=Depends(get_session)):
    """登出所有裝置"""
    user_id = session.user.user_id
    count = await session_manager.delete_user_sessions(user_id)
    return {"message": f"Logged out from {count} devices"}


# 輔助函數（示意）
def authenticate_user(username: str, password: str) -> bool:
    # 實際應該查詢資料庫並驗證密碼雜湊
    return True


def get_user_id(username: str) -> str:
    # 實際應該從資料庫獲取
    return f"user_{username}"


def get_user_roles(username: str) -> list[str]:
    # 實際應該從資料庫獲取
    return ["user"] if username != "admin" else ["admin", "user"]
```

## 配置選項

### SessionConfig

```python
SessionConfig(
    # Session 生命週期
    session_ttl=3600,              # Session TTL（秒）
    absolute_timeout=None,         # 絕對過期時間（秒）
    sliding_expiration=True,       # 滑動過期
    sliding_threshold=0.5,         # 滑動閾值（0.5 = TTL 過半時更新）

    # Token 來源（API-first 架構）
    header_name="X-Session-Token",
    use_bearer_token=True,
    token_source_priority=["header", "bearer"],  # 客戶端透過 header 傳送 token

    # 安全設定
    secret_key="...",              # 必須：至少 32 字元
    ip_binding=False,              # IP 綁定
    user_agent_binding=False,      # User-Agent 綁定
    regenerate_on_login=True,      # 登入後重新生成 ID

    # 後端設定
    backend_key_prefix="session:",

    # CSRF（可選，目前未完整實作）
    enable_csrf=False,
)
```

**注意**：此設計為 API-first 架構（前後端分離），Token 由客戶端管理並在每次請求的 Header 中傳送。客戶端應將 token 儲存在 `localStorage` 或 `sessionStorage` 中，並在請求時透過 `Authorization: Bearer <token>` 或 `X-Session-Token: <token>` header 傳送。

## 安全最佳實踐

### 1. Secret Key

```python
import secrets

# 生成安全的 secret key
secret_key = secrets.token_urlsafe(32)

config = SessionConfig(secret_key=secret_key)
```

### 2. HTTPS Only

生產環境務必使用 HTTPS 傳輸 token：

```python
config = SessionConfig(
    secret_key="...",
)
```

**客戶端注意事項**：
- 僅透過 HTTPS 傳輸 token
- 使用 `httpOnly` 選項（如果使用 cookie 儲存）來防止 XSS
- 避免在 URL 中傳遞 token

### 3. IP 綁定（可選）

提高安全性但可能影響使用者體驗（例如 IP 變動）：

```python
config = SessionConfig(
    secret_key="...",
    ip_binding=True,  # Session 綁定到客戶端 IP
)
```

### 4. 登入後重新生成 Session ID

防止 Session Fixation 攻擊：

```python
# 登入成功後
session, old_token = await session_manager.get_session(current_token)
session, new_token = await session_manager.regenerate_session_id(session)
```

## API 參考

### SessionManager

#### create_session()
```python
async def create_session(
    user: SessionUser | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    **extra_data,
) -> tuple[Session, str]:
```

#### get_session()
```python
async def get_session(
    token_string: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> Session:
```

#### update_session()
```python
async def update_session(session: Session) -> None:
```

#### delete_session()
```python
async def delete_session(session_id: str) -> None:
```

#### regenerate_session_id()
```python
async def regenerate_session_id(
    session: Session,
) -> tuple[Session, str]:
```

### Dependencies

```python
from fastapi_cachex.session import (
    get_session,          # 需要認證（無 session 時返回 401）
    get_optional_session, # 可選認證（無 session 時返回 None）
    require_session,      # 別名：get_session
)

# Type annotations
from fastapi_cachex.session.dependencies import (
    RequiredSession,
    OptionalSession,
    SessionDep,
)
```
