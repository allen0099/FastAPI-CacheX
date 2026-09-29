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
| `exp` | Expiration | ✅ | ✅ | 權杖的過期時間：Session 的 `expires_at`（因此會跟著滑動過期，且不會超過 `absolute_timeout`），沒有時退回 `iat + session_ttl` |
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

有效的 JWT 簽章是必要條件，但並不充分：解碼之後，`SessionManager.get_session()` 仍會從後端載入 Session，並在 Session 不存在、不在啟用狀態、已過期、超過 `absolute_timeout`，或未通過 IP／User-Agent 綁定檢查時拒絕它。任何解碼失敗都會以 `SessionTokenError` 拋出，Session 中介軟體（`FastAPICacheXSessionMiddleware`）會將其視為「沒有 Session」。

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
    - 支援滑動過期：Session 續期時，中介軟體會透過請求使用的傳輸方式送出帶有更新後 `exp` 的新權杖：由 `header_name` 指定的回應標頭（預設為 `X-Session-Token`），或對 Cookie 使用 `Set-Cookie`
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

如果你的應用程式需要額外的 JWT claim，請撰寫自己的序列化器，並透過 `SessionManager` 的 `token_serializer` 參數傳入實例。任何具有 `to_string(token) -> str` 與 `from_string(token_str) -> SessionToken` 方法的物件（即 `TokenSerializer` 協定）都可以；`from_string()` 遇到無效權杖時應拋出 `ValueError`，`SessionManager` 會將它轉換為 `SessionTokenError`。

下面的基底類別做的事與內建的 `JWTTokenSerializer` 相同，並為額外的 claim 留下兩個掛鉤。它從 `SessionConfig` 的公開欄位讀取設定並自行保存，而不是存取 `JWTTokenSerializer` 的私有屬性，因為那些屬性在任何版本都可能改變。它與內建序列化器一樣，在 `to_string()` 中採用 `token.expires_at`，讓 `exp` 持續跟著滑動過期。

<!-- fmt:off -->
```python
--8<-- "examples/session_jwt_claims.py:serializer"
```
<!-- fmt:on -->

PyJWT 預設會驗證簽章、`exp`、`iat` 與（存在時的）`nbf`，並在傳入 `issuer`／`audience` 時驗證 `iss`／`aud`。內建序列化器的兩項檢查在這裡沒有重複：它會拒絕非對稱的 `jwt_algorithm`，並在 `secret_key` 短於 HMAC 輸出長度時發出警告。這個類別同樣以 `secret_key` 簽署，因此請使用 `HS*` 演算法；若要使用非對稱演算法，請在類別中保存私鑰與公鑰，並在 `jwt.encode()` 與 `jwt.decode()` 中使用它們。

### 範例 1：加入 `jti` 與 `nbf` {#example-1-adding-jti-and-nbf}

```python
import uuid
from typing import Any

from fastapi_cachex.session.models import SessionToken


class ExtendedJWTSerializer(CustomClaimsJWTSerializer):
    """Adds the jti and nbf claims."""

    required_claims = ("jti", "nbf")

    def extra_claims(self, token: SessionToken) -> dict[str, Any]:
        return {
            "jti": str(uuid.uuid4()),  # 唯一的權杖 ID
            "nbf": int(token.issued_at.timestamp()),  # 生效時間 = 發行時間
        }
```

### 範例 2：加入多租戶的自訂 claim {#example-2-adding-multi-tenant-custom-claims}

<!-- fmt:off -->
```python
--8<-- "examples/session_jwt_claims.py:multi-tenant"
```
<!-- fmt:on -->

### 使用自訂序列化器 {#using-a-custom-serializer}

#### 做法 1：傳給 `SessionManager`（建議） {#option-1-pass-it-to-sessionmanager-recommended}

<!-- fmt:off -->
```python
--8<-- "examples/session_jwt_claims.py:setup"
```
<!-- fmt:on -->

提供 `token_serializer` 時，它會取代依 `token_format` 選擇的內建序列化器。但仍請保留 `token_format="jwt"`：若設為 `"simple"`，`SessionManager` 會對解析出的權杖額外執行自己的 HMAC 簽章檢查，而以 JWT 為基礎的序列化器產生的權杖通不過這項檢查。

範例使用 `MemoryBackend`，因此不需要伺服器即可執行；任何後端都可以，例如[後端](BACKENDS.md#closing-a-backend)中的 Redis 設定。

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

[`examples/session_jwt_claims.py`](https://github.com/allen0099/FastAPI-CacheX/blob/master/examples/session_jwt_claims.py) 把上面的各個部分組合成可執行的應用程式（程式碼註解為英文）：

<!-- fmt:off -->
```python
--8<-- "examples/session_jwt_claims.py"
```
<!-- fmt:on -->

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
payload = jwt.decode(token_str, self.secret, algorithms=[self.algorithm])
tenant_id = payload.get("tenant_id")  # 可能不存在或無效

# ✅ 正確：嚴格驗證
payload = jwt.decode(
    token_str,
    self.secret,
    algorithms=[self.algorithm],
    options={"require": ["sid", "iat", "exp", "tenant_id"]},
)
if payload["tenant_id"] != self.tenant_id:
    raise ValueError("Invalid tenant_id")
```

### 4. 金鑰輪替 {#4-key-rotation}

若要支援金鑰輪替，可以使用 `kid`（Key ID）標頭參數。以下是以 `CustomClaimsJWTSerializer` 為基礎的概略示意；`payload` 與 `kwargs` 的建立方式與它的 `to_string()`、`from_string()` 相同：

```python
class KeyRotationJWTSerializer(CustomClaimsJWTSerializer):
    def __init__(
        self, config: SessionConfig, keys: dict[str, str], current_key_id: str
    ) -> None:
        super().__init__(config)
        # 金鑰 ID -> 密鑰。舊金鑰請保留到它簽署的權杖都過期為止。
        self.keys = keys
        self.current_key_id = current_key_id

    def to_string(self, token: SessionToken) -> str:
        # 以目前的金鑰簽署，並在標頭中註明它
        return jwt.encode(
            payload,
            self.keys[self.current_key_id],
            algorithm=self.algorithm,
            headers={"kid": self.current_key_id},
        )

    def from_string(self, token_str: str) -> SessionToken:
        # 從（尚未驗證的）標頭讀取 kid，並挑選對應的金鑰
        kid = jwt.get_unverified_header(token_str).get("kid")
        key = self.keys.get(kid)
        if key is None:
            msg = "Unknown key ID"
            raise ValueError(msg)

        payload = jwt.decode(token_str, key, algorithms=[self.algorithm], **kwargs)
        # ...
```

## 測試建議 {#testing-recommendations}

為你的自訂序列化器加上測試：

```python
import jwt
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
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["tenant_id"] == "test-tenant"

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

A：目前不行；自訂 claim 需要自訂的 `token_serializer`，例如[擴充指南](#extension-guide-adding-custom-claims)中的類別。未來版本或許會加入像下面這樣的設定選項（這只是假設，目前並不存在，而且 `SessionConfig` 會拒絕未知的欄位）：

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
