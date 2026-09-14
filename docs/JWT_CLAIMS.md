# JWT Claims 實作說明與擴展指南

## 概述

FastAPI-CacheX 的 JWT token serializer 實作了基本的 JWT claims 以支援安全的 session token 傳輸。本文件說明：

1. 為什麼我們沒有實作完整的 JWT claims（如 `jti`、`nbf`）
2. 當前實作的設計考量
3. 如何擴展以新增自訂 claims

## 當前實作的 JWT Claims

### 已實作的標準 Claims

`JWTTokenSerializer` 實作了以下 JWT claims：

| Claim | 名稱 | 必需 | 驗證 | 說明 |
|-------|------|------|------|------|
| `sid` | Session ID | ✅ | ✅ | 自訂 claim，用於對應伺服器端 session |
| `iat` | Issued At | ✅ | ✅ | Token 簽發時間（RFC 7519） |
| `exp` | Expiration | ✅ | ✅ | Token 過期時間（`iat + session_ttl`） |
| `iss` | Issuer | ⚠️ | ✅ | Token 簽發者（可選，需配置） |
| `aud` | Audience | ⚠️ | ✅ | Token 目標受眾（可選，需配置） |

### 未實作的標準 Claims

以下是 RFC 7519 定義但**未實作**的可選 claims：

| Claim | 名稱 | 用途 | 為何未實作 |
|-------|------|------|-----------|
| `jti` | JWT ID | Token 唯一識別碼，防止重放攻擊 | Stateful session 模型已透過伺服器端狀態處理 |
| `nbf` | Not Before | Token 生效時間 | Session 通常立即生效，不需要延遲生效 |
| `sub` | Subject | 主體識別碼（通常是使用者 ID） | 使用自訂 `sid` claim 表示 session ID 更清晰 |

## 設計理念

### Stateful Session vs Stateless JWT

FastAPI-CacheX 採用 **stateful session** 模型，這與純 stateless JWT 有根本性差異：

```
┌─────────────────────────────────────────────────────────┐
│  FastAPI-CacheX Session Model (Stateful)                │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌──────────┐         ┌──────────┐        ┌──────────┐ │
│  │  Client  │  JWT    │  Server  │        │  Redis/  │ │
│  │          │ ──────> │          │ ────>  │  Cache   │ │
│  │          │  (sid)  │          │ lookup │          │ │
│  └──────────┘         └──────────┘        └──────────┘ │
│                                                           │
│  JWT 只攜帶 session ID (sid)                            │
│  實際 session 資料儲存在伺服器端                        │
│  可立即撤銷（刪除 cache 中的 session）                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Traditional Stateless JWT (NOT used by CacheX)         │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌──────────┐         ┌──────────┐                      │
│  │  Client  │  JWT    │  Server  │                      │
│  │          │ ──────> │          │                      │
│  │          │ (all)   │          │                      │
│  └──────────┘         └──────────┘                      │
│                                                           │
│  JWT 包含所有使用者資訊和權限                           │
│  伺服器無狀態，無法撤銷 token                           │
│  需要 jti + blacklist 才能撤銷                          │
└─────────────────────────────────────────────────────────┘
```

### 為何選擇 Stateful Session

#### ✅ 優點

1. **即時撤銷**
   - 透過 `SessionManager.delete_session()` 立即失效
   - 不需要維護 token 黑名單
   - 不需要 `jti` claim 和 blacklist 系統

2. **敏感資料保護**
   - Session 資料（包含 user info）儲存在伺服器端
   - JWT 只包含最小資訊（session ID）
   - 降低 JWT 洩漏的風險

3. **靈活的 Session 管理**
   - 支援 sliding expiration（滑動過期）
   - 支援 session 資料即時更新
   - 支援 flash messages 等功能

4. **Token 體積小**
   - JWT 只需攜帶 `sid` 和時間戳記
   - 減少網路傳輸開銷
   - 適合 API-first 架構的頻繁請求

#### ⚠️ 權衡

1. **需要後端儲存**
   - 需要 Redis/Memcached/Memory backend
   - 橫向擴展需要共享 cache（如 Redis cluster）

2. **每次請求需查詢 cache**
   - 增加一次 cache lookup
   - 但現代 cache 系統（Redis）非常快速（sub-millisecond）

### 為何不需要某些 Claims

#### `jti` (JWT ID)

**用途**：為每個 JWT 生成唯一 ID，用於：
- Token 黑名單（blacklist）
- 防止 token 重放攻擊
- 追蹤個別 token

**為何不需要**：
```python
# Stateless JWT 需要 jti + blacklist
jwt_payload = {"jti": "uuid-1234", "user_id": "123", ...}
# 撤銷時：將 jti 加入 blacklist，每次驗證時檢查

# FastAPI-CacheX stateful session
jwt_payload = {"sid": "session-abc123"}
# 撤銷時：直接刪除 cache 中的 session
await session_manager.delete_session("session-abc123")
# 下次請求時，cache lookup 失敗，自動拒絕
```

#### `nbf` (Not Before)

**用途**：指定 token 生效時間，用於：
- 預先簽發未來使用的 token
- 時間同步問題的容忍

**為何不需要**：
- Session 通常在建立時立即生效
- 如需延遲生效，應在應用邏輯層處理
- `leeway` 參數已處理時間同步問題

#### `sub` (Subject)

**用途**：識別 token 的主體（通常是使用者 ID）

**為何使用 `sid` 取代**：
- `sub` 通常表示**不可變**的使用者識別碼
- `sid` 表示**可變**的 session 識別碼
- Session regeneration 時 `sid` 會改變，但 `user_id` 不變
- 使用 `sid` 語意更清晰

## 擴展指南：新增自訂 Claims

如果您的應用需要額外的 JWT claims，可以透過繼承 `JWTTokenSerializer` 來實作。

### 範例 1：新增 `jti` 和 `nbf`

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi_cachex.session.token_serializers import JWTTokenSerializer
from fastapi_cachex.session.models import SessionToken


class ExtendedJWTSerializer(JWTTokenSerializer):
    """擴展 JWT serializer，新增 jti 和 nbf claims。"""

    def to_string(self, token: SessionToken) -> str:
        """編碼 SessionToken 為 JWT，包含 jti 和 nbf。"""
        iat = int(token.issued_at.timestamp())
        exp = iat + int(self._session_ttl)

        payload: dict[str, object] = {
            "sid": token.session_id,
            "iat": iat,
            "exp": exp,
            "jti": str(uuid.uuid4()),  # 唯一 token ID
            "nbf": iat,  # Not before = issued at
        }

        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience

        encoded = self.jwt_encoder.encode(
            payload, self._secret, algorithm=self._algorithm
        )
        return str(encoded)

    def from_string(self, token_str: str) -> SessionToken:
        """解碼並驗證 JWT，包含 jti 和 nbf 驗證。"""
        options = {
            "require": ["sid", "iat", "exp", "jti"],  # 要求 jti
            "verify_signature": True,
            "verify_exp": True,
            "verify_iat": True,
            "verify_nbf": True,  # 驗證 nbf
        }

        kwargs: dict[str, object] = {
            "algorithms": [self._algorithm],
            "options": options,
            "leeway": self._leeway,
            "key": self._secret,
        }

        if self._issuer:
            kwargs["issuer"] = self._issuer
        if self._audience:
            kwargs["audience"] = self._audience

        try:
            payload = self.jwt_encoder.decode(token_str, **kwargs)
        except Exception as e:
            msg = "Invalid JWT token"
            raise ValueError(msg) from e

        # 提取標準欄位
        sid = str(payload["sid"])
        iat = int(payload["iat"])
        issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)

        # 可選：記錄 jti 用於審計
        jti = payload.get("jti")
        # logger.info(f"JWT decoded: sid={sid}, jti={jti}")

        return SessionToken(session_id=sid, signature="", issued_at=issued_at)
```

### 範例 2：新增多租戶自訂 Claims

```python
from __future__ import annotations

from datetime import datetime, timezone

from fastapi_cachex.session.token_serializers import JWTTokenSerializer
from fastapi_cachex.session.models import SessionToken


class MultiTenantJWTSerializer(JWTTokenSerializer):
    """多租戶 JWT serializer，新增 tenant_id 和 api_version。"""

    def __init__(
        self, config, tenant_id: str, api_version: str = "v1", jwt_module=None
    ):
        super().__init__(config, jwt_module)
        self.tenant_id = tenant_id
        self.api_version = api_version

    def to_string(self, token: SessionToken) -> str:
        """編碼 SessionToken 為 JWT，包含租戶資訊。"""
        iat = int(token.issued_at.timestamp())
        exp = iat + int(self._session_ttl)

        payload: dict[str, object] = {
            "sid": token.session_id,
            "iat": iat,
            "exp": exp,
            # 自訂 claims
            "tenant_id": self.tenant_id,
            "api_version": self.api_version,
        }

        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience

        encoded = self.jwt_encoder.encode(
            payload, self._secret, algorithm=self._algorithm
        )
        return str(encoded)

    def from_string(self, token_str: str) -> SessionToken:
        """解碼並驗證 JWT，驗證租戶資訊。"""
        options = {
            "require": ["sid", "iat", "exp", "tenant_id", "api_version"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_iat": True,
        }

        kwargs: dict[str, object] = {
            "algorithms": [self._algorithm],
            "options": options,
            "leeway": self._leeway,
            "key": self._secret,
        }

        if self._issuer:
            kwargs["issuer"] = self._issuer
        if self._audience:
            kwargs["audience"] = self._audience

        try:
            payload = self.jwt_encoder.decode(token_str, **kwargs)
        except Exception as e:
            msg = "Invalid JWT token"
            raise ValueError(msg) from e

        # 驗證租戶資訊
        if payload["tenant_id"] != self.tenant_id:
            msg = f"Invalid tenant_id: expected {self.tenant_id}, got {payload['tenant_id']}"
            raise ValueError(msg)

        if payload["api_version"] != self.api_version:
            msg = f"Unsupported API version: {payload['api_version']}"
            raise ValueError(msg)

        # 提取標準欄位
        sid = str(payload["sid"])
        iat = int(payload["iat"])
        issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)

        return SessionToken(session_id=sid, signature="", issued_at=issued_at)
```

### 使用自訂 Serializer

#### 方法 1：透過 SessionManager 初始化參數（推薦）

```python
from fastapi import FastAPI
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.session import SessionManager, SessionConfig, SessionMiddleware

app = FastAPI()

# 設定 backend 和 config
backend = AsyncRedisCacheBackend(host="localhost", port=6379)
config = SessionConfig(
    secret_key="your-secret-key-min-32-chars",
    token_format="jwt",
    jwt_algorithm="HS256",
    jwt_issuer="your-company",
    jwt_audience="your-api",
)

# 建立自訂 serializer
custom_serializer = MultiTenantJWTSerializer(
    config=config,
    tenant_id="acme-corp",
    api_version="v2",
)

# 初始化 SessionManager
manager = SessionManager(backend, config, custom_serializer)

# 新增 middleware
app.add_middleware(
    SessionMiddleware,
    session_manager=manager,
    config=config,
)
```

#### 方法 2：繼承 SessionManager（進階）

```python
from fastapi_cachex.session import SessionManager


class MultiTenantSessionManager(SessionManager):
    """支援多租戶的 SessionManager。"""

    def __init__(self, backend, config, tenant_id: str):
        super().__init__(backend, config)

        # 替換 token serializer
        if config.token_format == "jwt":
            self._token_serializer = MultiTenantJWTSerializer(
                config=config,
                tenant_id=tenant_id,
            )


# 使用
manager = MultiTenantSessionManager(backend, config, tenant_id="acme-corp")
```

## 完整應用範例

```python
from __future__ import annotations

from fastapi import FastAPI, Depends, HTTPException
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.session import (
    SessionManager,
    SessionMiddleware,
    SessionConfig,
    SessionUser,
    get_session,
)

# 使用上面定義的 MultiTenantJWTSerializer

app = FastAPI()

# 初始化
backend = AsyncRedisCacheBackend(host="localhost", port=6379)
config = SessionConfig(
    secret_key="your-secret-key-min-32-chars-long!!",
    token_format="jwt",
    jwt_algorithm="HS256",
    jwt_issuer="acme-corp",
    jwt_audience="acme-api",
)

# 建立自訂 serializer
serializer = MultiTenantJWTSerializer(
    config=config,
    tenant_id="acme-corp",
    api_version="v2",
)

manager = SessionManager(backend, config)
manager._token_serializer = serializer

app.add_middleware(
    SessionMiddleware,
    session_manager=manager,
    config=config,
)


@app.post("/auth/login")
async def login(username: str, password: str):
    """登入端點，返回包含 tenant_id 的 JWT。"""
    # 驗證使用者（省略）
    if username != "admin":
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user = SessionUser(user_id="123", username=username)
    session, token = await manager.create_session(user=user)

    # Token 現在包含 tenant_id 和 api_version claims
    return {
        "token": token,
        "token_type": "bearer",
        "tenant_id": "acme-corp",  # 也可以從 config 讀取
    }


@app.get("/api/profile")
async def get_profile(session=Depends(get_session)):
    """受保護的端點，自動驗證 tenant_id。"""
    # JWT 已在解碼時驗證 tenant_id 和 api_version
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
    }
```

## 安全考量

### 1. Token 大小

新增更多 claims 會增加 JWT 大小，影響：
- 網路傳輸開銷
- Cookie 大小限制（如果使用 cookie）
- 效能

**建議**：只新增必要的 claims，避免在 JWT 中包含大量資料。

### 2. 敏感資料

不要在 JWT 中儲存敏感資料（如密碼、信用卡號）：
- JWT 可以被解碼（base64）
- 即使有簽名，內容仍可見
- 使用 server-side session 儲存敏感資料

### 3. Claims 驗證

自訂 claims 務必在 `from_string()` 中驗證：
```python
# ❌ 不好：沒有驗證
payload = self.jwt_encoder.decode(token_str, **kwargs)
tenant_id = payload.get("tenant_id")  # 可能不存在或無效

# ✅ 好：嚴格驗證
options = {"require": ["sid", "iat", "exp", "tenant_id"]}
payload = self.jwt_encoder.decode(token_str, **kwargs)
if payload["tenant_id"] != self.expected_tenant_id:
    raise ValueError("Invalid tenant_id")
```

### 4. Key Rotation

如需支援金鑰輪替（key rotation），可使用 `kid` (Key ID) claim：

```python
class KeyRotationJWTSerializer(JWTTokenSerializer):
    def __init__(self, config, key_id: str, jwt_module=None):
        super().__init__(config, jwt_module)
        self.key_id = key_id

    def to_string(self, token: SessionToken) -> str:
        # 新增 kid 到 JWT header
        encoded = self.jwt_encoder.encode(
            payload,
            self._secret,
            algorithm=self._algorithm,
            headers={"kid": self.key_id},
        )
        return str(encoded)

    def from_string(self, token_str: str) -> SessionToken:
        # 解析 header 以獲取 kid
        header = self.jwt_encoder.get_unverified_header(token_str)
        kid = header.get("kid")

        # 根據 kid 選擇對應的 key
        key = self._get_key_by_id(kid)

        payload = self.jwt_encoder.decode(token_str, key=key, **kwargs)
        # ...
```

## 測試建議

為自訂 serializer 新增測試：

```python
import pytest
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import SessionManager, SessionConfig, SessionUser


@pytest.mark.asyncio
async def test_custom_claims_included():
    """測試自訂 claims 是否包含在 JWT 中。"""
    backend = MemoryBackend()
    config = SessionConfig(secret_key="a" * 32, token_format="jwt")

    serializer = MultiTenantJWTSerializer(
        config=config,
        tenant_id="test-tenant",
        api_version="v1",
    )

    manager = SessionManager(backend, config)
    manager._token_serializer = serializer

    user = SessionUser(user_id="u1", username="alice")
    session, token = await manager.create_session(user=user)

    # 驗證 token 可以被解碼
    retrieved = await manager.get_session(token)
    assert retrieved.session_id == session.session_id


@pytest.mark.asyncio
async def test_custom_claims_validated():
    """測試自訂 claims 驗證失敗時被拒絕。"""
    backend = MemoryBackend()
    config = SessionConfig(secret_key="a" * 32, token_format="jwt")

    # 建立 token with tenant_id="tenant-1"
    serializer1 = MultiTenantJWTSerializer(config, tenant_id="tenant-1")
    manager1 = SessionManager(backend, config)
    manager1._token_serializer = serializer1
    _session, token = await manager1.create_session(user=SessionUser(user_id="u1"))

    # 嘗試用 tenant_id="tenant-2" 驗證（應失敗）
    serializer2 = MultiTenantJWTSerializer(config, tenant_id="tenant-2")
    manager2 = SessionManager(backend, config)
    manager2._token_serializer = serializer2

    with pytest.raises(ValueError, match="Invalid tenant_id"):
        await manager2.get_session(token)
```

## 常見問題

### Q: 為什麼不預設實作 `jti`？

A: `jti` 主要用於 stateless JWT 的 token 撤銷（blacklist）。FastAPI-CacheX 使用 stateful session，可以直接刪除伺服器端的 session 資料來撤銷 token，不需要額外的 blacklist 機制。

### Q: 我需要 `nbf` 嗎？

A: 大多數情況下不需要。`nbf` 用於預先簽發但延遲生效的 token。如果您的應用需要這個功能，建議在應用邏輯層處理（例如在 session.data 中記錄生效時間），而不是在 JWT 層面。

### Q: 能否在不修改程式碼的情況下新增 claims？

A: 目前需要透過繼承 `JWTTokenSerializer` 來新增自訂 claims。未來版本可能會考慮新增配置選項，例如：
```python
SessionConfig(
    token_format="jwt",
    jwt_custom_claims={"tenant_id": "acme", "version": "v1"},
)
```
但這會增加複雜度。目前的設計提供了足夠的靈活性，同時保持程式碼簡潔。

### Q: 自訂 claims 會影響效能嗎？

A: 影響很小。JWT 編碼/解碼的效能主要取決於：
1. 加密演算法（HS256 很快）
2. Token 大小（更多 claims = 更大）
3. 網路傳輸（更大的 token）

只要不新增大量資料，影響可以忽略。

### Q: 如何在 JWT 中包含使用者權限？

A: 不建議在 JWT 中包含權限資訊。FastAPI-CacheX 採用 stateful session，應該：
```python
# ✅ 推薦：儲存在 server-side session
session.user.roles = ["admin", "editor"]
session.data["permissions"] = ["read", "write", "delete"]
await manager.update_session(session)

# ❌ 不推薦：放在 JWT claims
# 權限變更時無法即時更新，除非撤銷所有現有 token
```

## 參考資料

- [RFC 7519 - JSON Web Token (JWT)](https://datatracker.ietf.org/doc/html/rfc7519)
- [PyJWT Documentation](https://pyjwt.readthedocs.io/)
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [FastAPI-CacheX Session Documentation](SESSION.md)

## 總結

FastAPI-CacheX 的 JWT 實作專注於 **stateful session** 場景，提供：

✅ **已實作**：基本 JWT claims（sid, iat, exp, iss, aud）
✅ **已實作**：簽名驗證與過期檢查
✅ **已實作**：可擴展架構（透過繼承）

⚠️ **未實作**：jti, nbf, sub（這些在 stateful session 中不是必需的）

🔧 **可擴展**：開發者可以輕鬆新增自訂 claims（見本文件範例）

這種設計在安全性、效能和靈活性之間取得了良好的平衡。如果您的應用有特殊需求，請參考本文件的擴展範例。
