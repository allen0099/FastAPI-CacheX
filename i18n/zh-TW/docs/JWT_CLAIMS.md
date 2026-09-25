# JWT claims：實作說明與擴充指南 {#jwt-claims-implementation-notes-and-extension-guide}

## 概觀 {#overview}

FastAPI-CacheX 的 JWT 權杖序列化器只實作了最小的一組 JWT claim，用來安全地承載 Session 權杖。本文件說明：

1. 為什麼我們不實作完整的 JWT claim（例如 `jti` 與 `nbf`）
2. 目前實作背後的設計考量
3. 如何以自訂 claim 擴充序列化器

實作位於 [`fastapi_cachex/session/token_serializers.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/fastapi_cachex/session/token_serializers.py)。JWT 支援需要安裝選用的 extra：`pip install "fastapi-cachex[jwt]"`。

## 目前實作中的 JWT claim {#jwt-claims-in-the-current-implementation}

### 已實作的標準 claim {#implemented-standard-claims}

`JWTTokenSerializer` 實作了下列 JWT claim：

| Claim | 名稱 | 必要 | 驗證 | 說明 |
|-------|------|------|------|------|
| `sid` | Session ID | ✅ | ✅ | 自訂 claim，對應到伺服器端的 Session |
| `iat` | Issued At | ✅ | ✅ | 權杖的發行時間（RFC 7519）；若位於未來（超出 `jwt_leeway`）則拒絕 |
| `exp` | Expiration | ✅ | ✅ | 權杖的過期時間：Session 的 `expires_at`（因此會跟著滑動過期），沒有時退回 `iat + session_ttl` |
| `iss` | Issuer | ⚠️ | ✅ | 權杖發行者（選用；只有設定 `jwt_issuer` 時才會發行並驗證） |
| `aud` | Audience | ⚠️ | ✅ | 預期的受眾（選用；只有設定 `jwt_audience` 時才會發行並驗證） |

### 相關的 `SessionConfig` 欄位 {#related-sessionconfig-fields}

| 欄位 | 預設值 | 說明 |
|------|--------|------|
| `token_format` | `"simple"` | 設為 `"jwt"` 以使用 `JWTTokenSerializer` |
| `secret_key` | （必填） | 簽署金鑰，至少 32 個字元；同時用於簽署與驗證 JWT |
| `jwt_algorithm` | `"HS256"` | 簽章演算法；必須是支援的值之一（會拒絕 `none`） |
| `jwt_issuer` | `None` | 預期的 `iss`；設定時會發行並驗證 |
| `jwt_audience` | `None` | 預期的 `aud`；設定時會發行並驗證 |
| `jwt_leeway` | `0` | 驗證 `exp`／`iat` 時容許的誤差秒數 |
| `session_ttl` | `3600` | Session 存活時間（秒）；Session 沒有 `expires_at` 時用於計算 `exp` |

> [!NOTE]
> **非對稱演算法：** `jwt_algorithm` 接受 `HS*`、`RS*`、`ES*`、`PS*` 與 `EdDSA`，但內建的序列化器以單一的 `secret_key` 字串簽署與驗證，因此只支援 HMAC 演算法（`HS256`、`HS384`、`HS512`）。以非對稱演算法建立 `SessionManager` 卻沒有提供自訂序列化器時，會拋出 `ValueError`。若要使用非對稱演算法，請傳入自訂的 `token_serializer`，以私鑰編碼、以對應的公鑰解碼（見[擴充指南](#extension-guide-adding-custom-claims)）。

### 未實作的標準 claim {#standard-claims-that-are-not-implemented}

下列 RFC 7519 定義的選用 claim **並未實作**：

| Claim | 名稱 | 用途 | 未實作的原因 |
|-------|------|------|--------------|
| `jti` | JWT ID | 權杖的唯一識別碼，防止重送攻擊 | 有狀態的 Session 模型已透過伺服器端狀態處理這件事 |
| `nbf` | Not Before | 權杖開始生效的時間 | Session 通常立即生效，不需要延後啟用 |
| `sub` | Subject | 主體識別碼（通常是使用者 ID） | 以自訂的 `sid` claim 表示 Session ID 更清楚 |

## 設計理由 {#design-rationale}

### 有狀態 Session 與無狀態 JWT {#stateful-session-vs-stateless-jwt}

FastAPI-CacheX 採用**有狀態 Session** 模型，與純粹無狀態的 JWT 有根本上的不同：

```
┌─────────────────────────────────────────────────────────┐
│  FastAPI-CacheX Session Model (Stateful)                │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────┐         ┌──────────┐        ┌──────────┐  │
│  │  Client  │  JWT    │  Server  │        │  Redis/  │  │
│  │          │ ──────> │          │ ────>  │  Cache   │  │
│  │          │  (sid)  │          │ lookup │          │  │
│  └──────────┘         └──────────┘        └──────────┘  │
│                                                         │
│  The JWT carries only the session ID (sid)              │
│  The actual session data is stored server-side          │
│  Revocable instantly (delete the session from cache)    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Traditional Stateless JWT (NOT used by CacheX)         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────┐         ┌──────────┐                      │
│  │  Client  │  JWT    │  Server  │                      │
│  │          │ ──────> │          │                      │
│  │          │ (all)   │          │                      │
│  └──────────┘         └──────────┘                      │
│                                                         │
│  The JWT contains all user info and permissions         │
│  The server is stateless and cannot revoke tokens       │
│  Revocation requires jti + a blacklist                  │
└─────────────────────────────────────────────────────────┘
```

有效的 JWT 簽章是必要條件，但並不充分：解碼之後，`SessionManager.get_session()` 仍會從後端載入 Session，並在 Session 不存在、不在啟用狀態、已過期、超過 `absolute_timeout`，或未通過 IP／User-Agent 綁定檢查時拒絕它。任何解碼失敗都會以 `SessionTokenError` 拋出，`SessionMiddleware` 會將其視為「沒有 Session」。

### 為什麼採用有狀態 Session {#why-a-stateful-session}

#### ✅ 優點 {#advantages}

1. **立即撤銷**
    - `SessionManager.delete_session()`（或 `invalidate_session()`）會立即生效
    - 不需要維護權杖黑名單
    - 不需要 `jti` claim 與黑名單系統

2. **保護敏感資料**
    - Session 資料（包括使用者資訊）存放在伺服器端
    - JWT 只包含最少的資訊（Session ID）
    - 降低 JWT 外洩的影響

3. **彈性的 Session 管理**
    - 支援滑動過期：Session 續期時，帶有更新後 `exp` 的新權杖會放在由 `header_name` 指定的回應標頭中回傳（預設為 `X-Session-Token`）
    - 支援即時更新 Session 資料
    - 支援 flash 訊息等功能

4. **權杖體積小**
    - JWT 只需要承載 `sid` 與時間戳記
    - 網路負擔較小
    - 很適合 API 優先架構中頻繁的請求

#### ⚠️ 取捨 {#trade-offs}

1. **需要後端儲存**
    - 需要 Redis、Memcached 或記憶體後端
    - 水平擴展需要共用的快取（例如 Redis 叢集）

2. **每個請求都需要查詢快取**
    - 每個請求多一次快取查詢
    - 但現代的快取系統（Redis）非常快（低於一毫秒）

### 為什麼不需要某些 claim {#why-some-claims-are-not-needed}

#### `jti`（JWT ID） {#jti-jwt-id}

**用途**：為每個 JWT 產生唯一的 ID，用於：

- 權杖黑名單
- 防止權杖重送攻擊
- 追蹤個別權杖

**為什麼不需要**：

```python
# 無狀態 JWT 需要 jti + 黑名單
jwt_payload = {"jti": "uuid-1234", "user_id": "123", ...}
# 撤銷方式：將 jti 加入黑名單，並在每次驗證時檢查

# FastAPI-CacheX 的有狀態 Session
jwt_payload = {"sid": "session-abc123"}
# 撤銷方式：直接從快取中刪除 Session
await session_manager.delete_session("session-abc123")
# 下一個請求查詢快取時找不到 Session，請求會自動被拒絕
```

#### `nbf`（Not Before） {#nbf-not-before}

**用途**：指定權杖開始生效的時間，用於：

- 預先發行權杖供日後使用
- 容忍時鐘偏差

**為什麼不需要**：

- Session 通常在建立後立即生效
- 若需要延後啟用，應該放在應用程式邏輯中處理
- `jwt_leeway` 設定已經處理了 `exp` 與 `iat` 的時鐘偏差

#### `sub`（Subject） {#sub-subject}

**用途**：識別權杖的主體（通常是使用者 ID）

**為什麼改用 `sid`**：

- `sub` 通常代表**不可變**的使用者識別碼
- `sid` 代表**可變**的 Session 識別碼
- `SessionManager.regenerate_session_id()` 會改變 `sid`，而 `user_id` 保持不變
- `sid` 讓語意更清楚

## 擴充指南：加入自訂 claim {#extension-guide-adding-custom-claims}

如果你的應用程式需要額外的 JWT claim，請繼承 `JWTTokenSerializer`，並透過 `SessionManager` 的 `token_serializer` 參數傳入實例。任何具有 `to_string(token) -> str` 與 `from_string(token_str) -> SessionToken` 方法的物件（即 `TokenSerializer` 協定）都可以；`from_string()` 遇到無效權杖時應拋出 `ValueError`，`SessionManager` 會將它轉換為 `SessionTokenError`。

下面的範例在 `to_string()` 中與內建序列化器一樣採用 `token.expires_at`，讓 `exp` 持續跟著滑動過期。

### 範例 1：加入 `jti` 與 `nbf` {#example-1-adding-jti-and-nbf}

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi_cachex.session.models import SessionToken
from fastapi_cachex.session.token_serializers import JWTTokenSerializer


class ExtendedJWTSerializer(JWTTokenSerializer):
    """Extended JWT serializer that adds the jti and nbf claims."""

    def to_string(self, token: SessionToken) -> str:
        """Encode a SessionToken as a JWT, including jti and nbf."""
        iat = int(token.issued_at.timestamp())
        if token.expires_at is not None:
            exp = int(token.expires_at.timestamp())
        else:
            exp = iat + int(self._session_ttl)

        payload: dict[str, object] = {
            "sid": token.session_id,
            "iat": iat,
            "exp": exp,
            "jti": str(uuid.uuid4()),  # 唯一的權杖 ID
            "nbf": iat,  # 生效時間 = 發行時間
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
        """Decode and verify a JWT, including jti and nbf validation."""
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

        # 取出標準欄位
        sid = str(payload["sid"])
        iat = int(payload["iat"])
        issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)

        # 選用：記錄 jti 以供稽核
        jti = payload.get("jti")
        # logger.info("JWT decoded: sid=%s, jti=%s", sid, jti)

        return SessionToken(session_id=sid, signature="", issued_at=issued_at)
```

### 範例 2：加入多租戶的自訂 claim {#example-2-adding-multi-tenant-custom-claims}

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi_cachex.session import SessionConfig
from fastapi_cachex.session.models import SessionToken
from fastapi_cachex.session.token_serializers import JWTTokenSerializer


class MultiTenantJWTSerializer(JWTTokenSerializer):
    """Multi-tenant JWT serializer that adds tenant_id and api_version."""

    def __init__(
        self,
        config: SessionConfig,
        tenant_id: str,
        api_version: str = "v1",
        jwt_module: Any | None = None,
    ) -> None:
        super().__init__(config, jwt_module)
        self.tenant_id = tenant_id
        self.api_version = api_version

    def to_string(self, token: SessionToken) -> str:
        """Encode a SessionToken as a JWT, including tenant information."""
        iat = int(token.issued_at.timestamp())
        if token.expires_at is not None:
            exp = int(token.expires_at.timestamp())
        else:
            exp = iat + int(self._session_ttl)

        payload: dict[str, object] = {
            "sid": token.session_id,
            "iat": iat,
            "exp": exp,
            # 自訂 claim
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
        """Decode and verify a JWT, validating the tenant information."""
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

        # 取出標準欄位
        sid = str(payload["sid"])
        iat = int(payload["iat"])
        issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)

        return SessionToken(session_id=sid, signature="", issued_at=issued_at)
```

### 使用自訂序列化器 {#using-a-custom-serializer}

#### 做法 1：傳給 `SessionManager`（建議） {#option-1-pass-it-to-sessionmanager-recommended}

```python
from fastapi import FastAPI

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.session import SessionConfig, SessionManager, SessionMiddleware

app = FastAPI()

# 建立後端與設定
backend = AsyncRedisCacheBackend(host="localhost", port=6379)
config = SessionConfig(
    secret_key="your-secret-key-at-least-32-characters",
    token_format="jwt",
    jwt_algorithm="HS256",
    jwt_issuer="your-company",
    jwt_audience="your-api",
)

# 建立自訂序列化器
custom_serializer = MultiTenantJWTSerializer(
    config=config,
    tenant_id="acme-corp",
    api_version="v2",
)

# 初始化 SessionManager
manager = SessionManager(backend, config, token_serializer=custom_serializer)

# 加入中介軟體
app.add_middleware(
    SessionMiddleware,
    session_manager=manager,
    config=config,
)
```

提供 `token_serializer` 時，它會取代依 `token_format` 選擇的內建序列化器。但仍請保留 `token_format="jwt"`：若設為 `"simple"`，`SessionManager` 會對解析出的權杖額外執行自己的 HMAC 簽章檢查，而以 JWT 為基礎的序列化器產生的權杖通不過這項檢查。

#### 做法 2：繼承 `SessionManager`（進階） {#option-2-subclass-sessionmanager-advanced}

```python
from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.session import SessionConfig, SessionManager


class MultiTenantSessionManager(SessionManager):
    """SessionManager with multi-tenant support."""

    def __init__(
        self, backend: BaseCacheBackend, config: SessionConfig, tenant_id: str
    ) -> None:
        super().__init__(
            backend,
            config,
            token_serializer=MultiTenantJWTSerializer(
                config=config, tenant_id=tenant_id
            ),
        )


# 使用方式
manager = MultiTenantSessionManager(backend, config, tenant_id="acme-corp")
```

不要在建構之後以指派私有屬性的方式替換序列化器；請透過 `token_serializer` 參數傳入，讓管理器在發行與解析權杖時都使用它。

## 完整應用程式範例 {#complete-application-example}

```python
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex.session import (
    Session,
    SessionConfig,
    SessionManager,
    SessionMiddleware,
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

# 建立自訂序列化器
serializer = MultiTenantJWTSerializer(
    config=config,
    tenant_id="acme-corp",
    api_version="v2",
)

manager = SessionManager(backend, config, token_serializer=serializer)

app.add_middleware(
    SessionMiddleware,
    session_manager=manager,
    config=config,
)


@app.post("/auth/login")
async def login(username: str, password: str) -> dict[str, str]:
    """Login endpoint that returns a JWT containing tenant_id."""
    # 驗證使用者（省略）
    if username != "admin":
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user = SessionUser(user_id="123", username=username)
    session, token = await manager.create_session(user=user)

    # 權杖現在包含 tenant_id 與 api_version claim
    return {
        "token": token,
        "token_type": "bearer",
        "tenant_id": "acme-corp",  # 也可以從設定讀取
    }


@app.get("/api/profile")
async def get_profile(session: Session = Depends(get_session)) -> dict[str, str | None]:
    """Protected endpoint; tenant_id is validated automatically."""
    # tenant_id 與 api_version 已在解碼 JWT 時驗證過。
    # 其他租戶的權杖會解碼失敗，因此中介軟體不會設定
    # Session，get_session 會回應 401。
    assert session.user is not None
    return {
        "user_id": session.user.user_id,
        "username": session.user.username,
    }
```

## 安全性考量 {#security-considerations}

### 1. 權杖大小 {#1-token-size}

加入更多 claim 會增加 JWT 的大小，進而影響：

- 網路負擔
- Cookie 大小限制（若權杖存放在 Cookie 中，例如使用 `FastAPICacheXSessionMiddleware` 時）
- 效能

**建議**：只加入需要的 claim，避免在 JWT 中放入大量資料。

### 2. 敏感資料 {#2-sensitive-data}

不要在 JWT 中存放敏感資料（例如密碼或信用卡號碼）：

- JWT 可以被解碼（它是 base64url 編碼）
- 即使有簽章，內容仍然可以讀取
- 請改將敏感資料存放在伺服器端的 Session 中

### 3. Claim 驗證 {#3-claim-validation}

一律在 `from_string()` 中驗證自訂 claim：

```python
# ❌ 錯誤：沒有驗證
payload = self.jwt_encoder.decode(token_str, **kwargs)
tenant_id = payload.get("tenant_id")  # 可能不存在或無效

# ✅ 正確：嚴格驗證
options = {"require": ["sid", "iat", "exp", "tenant_id"]}
payload = self.jwt_encoder.decode(token_str, **kwargs)
if payload["tenant_id"] != self.expected_tenant_id:
    raise ValueError("Invalid tenant_id")
```

### 4. 金鑰輪替 {#4-key-rotation}

若要支援金鑰輪替，可以使用 `kid`（Key ID）標頭參數。以下只是概略示意；`payload`、`kwargs` 與 `_get_key_by_id()` 需要你自行補上：

```python
class KeyRotationJWTSerializer(JWTTokenSerializer):
    def __init__(
        self, config: SessionConfig, key_id: str, jwt_module: Any | None = None
    ) -> None:
        super().__init__(config, jwt_module)
        self.key_id = key_id

    def to_string(self, token: SessionToken) -> str:
        # 在 JWT 標頭中加入 kid
        encoded = self.jwt_encoder.encode(
            payload,
            self._secret,
            algorithm=self._algorithm,
            headers={"kid": self.key_id},
        )
        return str(encoded)

    def from_string(self, token_str: str) -> SessionToken:
        # 解析標頭以取得 kid
        header = self.jwt_encoder.get_unverified_header(token_str)
        kid = header.get("kid")

        # 依 kid 選擇對應的金鑰
        key = self._get_key_by_id(kid)

        payload = self.jwt_encoder.decode(token_str, key=key, **kwargs)
        # ...
```

## 測試建議 {#testing-recommendations}

為你的自訂序列化器加上測試：

```python
import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session import SessionConfig, SessionManager, SessionUser
from fastapi_cachex.session.exceptions import SessionTokenError


@pytest.mark.asyncio
async def test_custom_claims_included():
    """Custom claims are included in the JWT and the token round-trips."""
    backend = MemoryBackend()
    config = SessionConfig(secret_key="a" * 32, token_format="jwt")

    serializer = MultiTenantJWTSerializer(
        config=config,
        tenant_id="test-tenant",
        api_version="v1",
    )
    manager = SessionManager(backend, config, token_serializer=serializer)

    user = SessionUser(user_id="u1", username="alice")
    session, token = await manager.create_session(user=user)

    # 權杖可以解碼，且帶有自訂 claim
    assert (
        serializer.jwt_encoder.decode(token, options={"verify_signature": False})[
            "tenant_id"
        ]
        == "test-tenant"
    )

    # get_session 回傳 (session, renewed_token)
    retrieved, _renewed = await manager.get_session(token)
    assert retrieved.session_id == session.session_id


@pytest.mark.asyncio
async def test_custom_claims_validated():
    """A token whose custom claims fail validation is rejected."""
    backend = MemoryBackend()
    config = SessionConfig(secret_key="a" * 32, token_format="jwt")

    # 建立 tenant_id="tenant-1" 的權杖
    manager1 = SessionManager(
        backend,
        config,
        token_serializer=MultiTenantJWTSerializer(config, tenant_id="tenant-1"),
    )
    _session, token = await manager1.create_session(user=SessionUser(user_id="u1"))

    # 嘗試以 tenant_id="tenant-2" 驗證它（必須失敗）
    manager2 = SessionManager(
        backend,
        config,
        token_serializer=MultiTenantJWTSerializer(config, tenant_id="tenant-2"),
    )

    # 序列化器的 ValueError 會以 SessionTokenError 呈現
    with pytest.raises(SessionTokenError, match="Invalid tenant_id"):
        await manager2.get_session(token)
```

## 常見問題 {#faq}

### Q：為什麼預設不實作 `jti`？ {#q-why-isnt-jti-implemented-by-default}

A：`jti` 主要用於撤銷無狀態的 JWT（透過黑名單）。FastAPI-CacheX 使用有狀態 Session，因此直接刪除伺服器端的 Session 資料就能撤銷權杖，不需要另外的黑名單機制。

### Q：我需要 `nbf` 嗎？ {#q-do-i-need-nbf}

A：大多數情況下不需要。`nbf` 用於預先發行、但稍後才生效的權杖。如果你的應用程式需要這個功能，我們建議在應用程式邏輯中處理（例如在 `session.data` 中記錄啟用時間），而不是在 JWT 層級處理。

### Q：可以不寫程式碼就加入 claim 嗎？ {#q-can-i-add-claims-without-writing-code}

A：目前不行；自訂 claim 需要繼承 `JWTTokenSerializer`。未來版本或許會加入像下面這樣的設定選項（這只是假設，目前並不存在，而且 `SessionConfig` 會拒絕未知的欄位）：

```python
SessionConfig(
    token_format="jwt",
    jwt_custom_claims={"tenant_id": "acme", "version": "v1"},
)
```

不過這會增加複雜度。目前的設計在保持程式碼簡單的同時，已提供足夠的彈性。

### Q：自訂 claim 會影響效能嗎？ {#q-do-custom-claims-affect-performance}

A：影響很小。JWT 編碼／解碼的效能主要取決於：

1. 簽章演算法（HS256 很快）
2. 權杖大小（claim 越多，權杖越大）
3. 網路傳輸（權杖越大，傳輸越多）

只要不加入大量資料，影響可以忽略不計。

### Q：如何在 JWT 中包含使用者權限？ {#q-how-do-i-include-user-permissions-in-the-jwt}

A：我們不建議將權限放在 JWT 中。FastAPI-CacheX 使用有狀態 Session，因此你應該：

```python
# ✅ 建議：存放在伺服器端的 Session 中
session.user.roles = ["admin", "editor"]
session.user.permissions = ["read", "write", "delete"]
await manager.update_session(session)

# ❌ 不建議：放在 JWT claim 中
# 除非撤銷所有現有的權杖，否則權限變更無法立即生效
```

## 參考資料 {#references}

- [RFC 7519 - JSON Web Token (JWT)](https://datatracker.ietf.org/doc/html/rfc7519)
- [PyJWT 文件](https://pyjwt.readthedocs.io/)
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [FastAPI-CacheX Session 文件](SESSION.md)

## 總結 {#summary}

FastAPI-CacheX 的 JWT 實作專注於**有狀態 Session** 的使用情境，並提供：

- ✅ **已實作**：基本的 JWT claim（`sid`、`iat`、`exp`、`iss`、`aud`）
- ✅ **已實作**：簽章驗證與過期檢查
- ✅ **已實作**：可擴充的設計（透過繼承與 `token_serializer` 參數）
- ⚠️ **未實作**：`jti`、`nbf`、`sub`（有狀態 Session 不需要它們）
- 🔧 **可擴充**：開發者可以輕鬆加入自訂 claim（見本文件中的範例）

這個設計在安全性、效能與彈性之間取得了良好的平衡。如果你的應用程式有特殊需求，請參考本文件中的擴充範例。
