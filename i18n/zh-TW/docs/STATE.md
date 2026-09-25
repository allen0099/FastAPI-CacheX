# State 管理擴充 {#state-management-extension}

`fastapi_cachex.state` 提供**一次性 state 權杖**，保護 OAuth / OIDC 授權流程免於 CSRF 攻擊。開始授權之前，先產生一個隨機 state 並存入快取後端；回呼（callback）回來時，再將它**消耗**掉。已消耗的 state 無法再使用第二次。

State 與 HTTP 快取存放在同一個後端，但使用自己的鍵前綴（預設為 `oauth_state:`），因此像 `CacheManager.clear_prefix()` 這類依命名空間的操作不會動到它們。不過後端層級的 `clear()`（例如 `BackendProxy.get().clear()`）會移除它們，因為它會清除後端命名空間底下的所有內容。

本指南中的所有內容也都可以從頂層的 `fastapi_cachex` 套件匯入。

## 快速開始 {#quick-start}

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from fastapi_cachex import BackendProxy
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.state import StateError, StateManagerDep

app = FastAPI()
BackendProxy.set(MemoryBackend())


@app.get("/login")
async def login(states: StateManagerDep):
    state = await states.create_state(metadata={"next": "/dashboard"})
    return RedirectResponse(
        f"https://provider.example.com/authorize?state={state}&client_id=..."
    )


@app.get("/callback")
async def callback(state: str, code: str, states: StateManagerDep):
    try:
        data = await states.consume_state(state)  # 一次性：取出時即刪除
    except StateError as e:  # 未知、已過期或格式錯誤的 state
        raise HTTPException(status_code=400, detail="Invalid state") from e

    # 以 code 換取權杖、建立 Session……
    return {"next": data.metadata.get("next", "/")}
```

## StateManager {#statemanager}

```python
from fastapi_cachex.state import StateManager

states = StateManager(
    backend=None,  # None 表示使用 BackendProxy.get()
    key_prefix="oauth_state:",  # 鍵前綴
    default_ttl=600,  # 預設：10 分鐘
)
```

當 `backend=None` 時，後端是在**建構 `StateManager` 時**解析，而不是每次呼叫時才解析。若尚未呼叫 `BackendProxy.set(...)`，建構子會拋出 `BackendNotFoundError`。請先設定後端。

### `create_state(ttl=None, metadata=None) -> str` {#create_statettlnone-metadatanone-str}

以 `secrets.token_urlsafe(32)`（256 位元熵）產生 state 字串，存入後端並回傳。`metadata` 是與 state 一併儲存的任意可 JSON 序列化 dict（例如授權完成後要重新導向的路徑）。省略 `ttl` 時使用 `default_ttl`。同一個 TTL 會同時作為後端 TTL 與 state 的 `expires_at`。

### `consume_state(state) -> StateData` {#consume_statestate-statedata}

**一次性消耗。** 項目透過後端的原子操作 `get_and_delete()` 取出並移除，因此當多個並行呼叫提交同一個 state 時，**只有一個**會取得它。重送的回呼無法通過第二次。

| 情況 | 行為 |
|------|------|
| 不存在、已被消耗，或已因後端 TTL 而被淘汰 | `InvalidStateError` |
| 已取出但超過其 `expires_at` | `StateExpiredError`（項目也已被刪除，不會殘留） |
| 已取出但內容不是有效的 `StateData` JSON | `StateDataError`（項目也已被刪除） |
| 其他情況 | 回傳 `StateData` |

一般情況下，後端 TTL 會先移除已過期的 state，因此過期的 state 通常會以 `InvalidStateError` 而非 `StateExpiredError` 呈現；兩者都請捕捉。在 Redis 與 Memcached 上，完全無法解碼成快取項目的儲存值會被後端視為未命中，同樣會以 `InvalidStateError` 呈現。

### `validate_state(state) -> bool` {#validate_statestate-bool}

不會消耗 state 的唯讀檢查：state 存在、可解析且尚未過期時回傳 `True`，否則回傳 `False`。它不會拋出 state 相關例外。

> [!WARNING]
> `validate_state()` **不會**消耗 state，因此單獨使用時無法防止重送攻擊。真正的防護是 `consume_state()`。`validate_state()` 只應用於與安全無關的判斷，例如「先探測，再決定要顯示哪個 UI」。

### `get_state_metadata(state) -> dict | None` {#get_state_metadatastate-dict-none}

同樣不會消耗 state。回傳建立時儲存的 `metadata`；若 state 不存在、已過期或無法解析，則回傳 `None`。

### `delete_state(state) -> bool` {#delete_statestate-bool}

手動刪除 state（例如使用者取消授權時）。回傳該 state 是否存在。它使用相同的原子操作 `get_and_delete()`，因此即使與 `consume_state()` 競爭，也最多只有一個呼叫者會得到 `True`。

## StateData {#statedata}

```python
class StateData(BaseModel):
    state: str  # state 字串本身
    created_at: datetime  # 建立時間（UTC）
    expires_at: datetime  # 過期時間（UTC）
    metadata: dict[str, Any]  # 建立時附加的 metadata
```

`expires_at` 是儲存在資料內的邏輯過期時間，與後端 TTL 無關。後端 TTL 到期時，項目就會消失；`expires_at` 則確保後端仍保留、但在邏輯上已過期的項目同樣會被拒絕。

## 依賴注入與 proxy {#dependency-injection-and-proxy}

```python
from fastapi_cachex.state import StateManagerDep, StateManagerProxy, get_state_manager


# 1. 直接使用型別註記（最常見）
@app.get("/login")
async def login(states: StateManagerDep): ...


# 2. 自訂實例（例如不同的前綴或 TTL）：在啟動時註冊，
#    依賴注入就會回傳它
StateManagerProxy.set(StateManager(key_prefix="csrf:", default_ttl=300))
```

尚未註冊任何實例時，`get_state_manager()`（`StateManagerDep` 背後的依賴項）會在第一次使用時延遲建立一個以 `BackendProxy` 的後端為基礎的預設 `StateManager`，並將它註冊。它不會退回使用 `MemoryBackend`：若尚未設定後端，請求會以 `BackendNotFoundError` 失敗。

## 例外 {#exceptions}

```
CacheXError
└── StateError
    ├── InvalidStateError   # 不存在或已被消耗
    ├── StateExpiredError   # 已過期
    └── StateDataError      # 內容格式錯誤
```

## 注意事項 {#notes}

- **後端必須在多個行程之間共用。** 多 worker 部署請使用 Redis 或 Memcached。使用 `MemoryBackend` 時，state 只存在於建立它的行程中，因此落到其他 worker 的授權回呼會失敗。
- **一次性保證來自後端的原子操作。** `get_and_delete()` 在 Redis 上是 `GETDEL`（需要 Redis 伺服器 6.2 或更新版本）；在 Memcached 上是先 get 再 `delete(noreply=False)`，只有 delete 成功的呼叫者勝出；在記憶體後端上則是在鎖內 `pop`。只實作抽象方法的自訂後端會退回使用 `BaseCacheBackend` 的非原子性版本，因此並行的重送可能兩邊都成功。這種情況請覆寫 `get_and_delete()`。
- 不要在 state 中存放敏感資料。`metadata` 會以明文 JSON 存放在快取後端中。
- **日誌中絕不會出現 state 本身。** 來自 `fastapi_cachex.state.manager` 的日誌以 `state_ref` 識別 state，也就是其 SHA-256 的前 12 個十六進位字元；你可以從已知的 state 計算出它來比對。未知或已過期的 state 以 INFO 等級記錄，因為那是常見的用戶端輸入；格式錯誤的儲存資料則以 WARNING 等級記錄一次。
