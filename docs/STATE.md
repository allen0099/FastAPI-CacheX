# State Management Extension

`fastapi_cachex.state` 提供**一次性 state token**,用來擋 OAuth / OIDC 授權流程的
CSRF：發起授權前先產生一枚隨機 state 並存進快取後端,callback 回來時**消費**它,
消費過的 state 不能再被使用第二次。

state 內容存在與 HTTP 快取同一組 backend 上,但使用獨立的金鑰前綴（預設
`oauth_state:`）,因此 `clear_prefix()` 等操作不會互相波及。

## 快速開始

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from fastapi_cachex import BackendProxy
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex.state import InvalidStateError, StateExpiredError, StateManagerDep

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
        data = await states.consume_state(state)  # 一次性：成功後即刪除
    except (InvalidStateError, StateExpiredError) as e:
        raise HTTPException(status_code=400, detail="Invalid state") from e

    # 換 token、建立 session ...
    return {"next": data.metadata.get("next", "/")}
```

## StateManager

```python
from fastapi_cachex.state import StateManager

states = StateManager(
    backend=None,  # None 代表使用 BackendProxy.get()
    key_prefix="oauth_state:",  # 金鑰前綴
    default_ttl=600,  # 預設 10 分鐘
)
```

### `create_state(ttl=None, metadata=None) -> str`

產生一枚 `secrets.token_urlsafe(32)`（256 bits 熵）的 state 字串並存入後端,回傳該字串。
`metadata` 是任意可 JSON 序列化的字典,會與 state 一起存放（例如授權後要導回的路徑）。
`ttl` 未指定時用 `default_ttl`。

### `consume_state(state) -> StateData`

**一次性消費**。以後端的原子操作 `get_and_delete()` 取出並刪除,所以多個並行呼叫
同一枚 state 時**只有一個**拿得到 —— 重播的 callback 無法通過第二次。

| 情況 | 行為 |
|------|------|
| 不存在／已被消費 | `InvalidStateError` |
| 取得但已過期 | `StateExpiredError`（條目同時已被刪除,不會留下殘骸） |
| 內容無法解析 | `StateDataError`（條目同樣已被刪除） |
| 正常 | 回傳 `StateData` |

### `validate_state(state) -> bool`

只看不消費：state 存在、可解析且未過期時回 `True`,否則 `False`。不會拋例外。

> [!WARNING]
> `validate_state()` **不會**把 state 消費掉,所以它本身擋不住重播。
> 真正的防護是 `consume_state()`；`validate_state()` 只適合用在「先探測、再決定 UI」
> 這類非安全判斷。

### `get_state_metadata(state) -> dict | None`

同樣不消費,回傳當初存入的 `metadata`；state 不存在／過期／無法解析時回 `None`。

### `delete_state(state) -> bool`

手動刪除（例如使用者取消授權）。回傳「原本是否存在」。

## StateData

```python
class StateData(BaseModel):
    state: str  # state 字串本身
    created_at: datetime  # 建立時間（UTC）
    expires_at: datetime  # 過期時間（UTC）
    metadata: dict[str, Any]  # 建立時附帶的中繼資料
```

`expires_at` 是存在資料內的邏輯過期時間,與後端 TTL 各自獨立：後端 TTL 到期會讓條目消失,
而 `expires_at` 讓「後端還留著但邏輯上已過期」的條目一樣被拒絕。

## 依賴注入與 Proxy

```python
from fastapi_cachex.state import StateManagerDep, StateManagerProxy, get_state_manager


# 1. 直接用型別註解（最常見）
@app.get("/login")
async def login(states: StateManagerDep): ...


# 2. 自訂實例（例如換前綴或 TTL）：啟動時註冊,依賴注入就會拿到它
StateManagerProxy.set(StateManager(key_prefix="csrf:", default_ttl=300))
```

`get_state_manager()`（`StateManagerDep` 背後的依賴）在沒有註冊過實例時,會延遲建立一個
預設 `StateManager`（使用 `BackendProxy` 的後端）並註冊起來。

## 例外

```
CacheXError
└── StateError
    ├── InvalidStateError   # 不存在或已被消費
    ├── StateExpiredError   # 已過期
    └── StateDataError      # 內容格式不正確
```

## 注意事項

- **後端必須是跨進程共享的**。多 worker 部署時用 Redis 或 Memcached；`MemoryBackend`
  的 state 只存在於產生它的那個進程,授權 callback 打到別的 worker 就會失敗。
- **一次性保證來自後端的原子操作**：`get_and_delete()` 在 Redis 是 `GETDEL`、
  Memcached 是 get + `delete(noreply=False)` 的勝者判定、記憶體後端則在鎖內 pop。
  自訂後端若只實作抽象方法,會落到 `BaseCacheBackend` 的非原子回退版本,
  並行重播就有機會兩邊都成功 —— 請自行覆寫。
- state 不該存放敏感資料；`metadata` 會以 JSON 明文存在快取後端。
