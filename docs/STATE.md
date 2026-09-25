# State Management Extension

`fastapi_cachex.state` provides **one-time state tokens** that protect OAuth / OIDC
authorization flows against CSRF. Before starting the authorization, generate a random
state and store it in the cache backend. When the callback comes back, **consume** it.
A consumed state cannot be used a second time.

States live on the same backend as the HTTP cache but under their own key prefix
(`oauth_state:` by default), so namespaced operations such as `CacheManager.clear_prefix()`
leave them alone. A backend-wide `clear()` (for example `BackendProxy.get().clear()`)
does remove them, because it clears everything under the backend's namespace.

Everything in this guide can also be imported from the top-level `fastapi_cachex` package.

## Quick start

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
        data = await states.consume_state(state)  # one-time: deleted on retrieval
    except (InvalidStateError, StateExpiredError) as e:
        raise HTTPException(status_code=400, detail="Invalid state") from e

    # Exchange the code for tokens, create a session ...
    return {"next": data.metadata.get("next", "/")}
```

## StateManager

```python
from fastapi_cachex.state import StateManager

states = StateManager(
    backend=None,  # None means use BackendProxy.get()
    key_prefix="oauth_state:",  # key prefix
    default_ttl=600,  # default: 10 minutes
)
```

With `backend=None` the backend is resolved **when the `StateManager` is constructed**,
not on each call. If `BackendProxy.set(...)` has not been called yet, the constructor
raises `BackendNotFoundError`. Configure the backend first.

### `create_state(ttl=None, metadata=None) -> str`

Generates a state string with `secrets.token_urlsafe(32)` (256 bits of entropy), stores it
in the backend and returns it. `metadata` is an arbitrary JSON-serializable dict stored
alongside the state (for example, the path to redirect to after authorization). When `ttl`
is omitted, `default_ttl` is used. The same TTL is applied both as the backend TTL and as
the state's `expires_at`.

### `consume_state(state) -> StateData`

**One-time consumption.** The entry is retrieved and removed with the backend's atomic
`get_and_delete()`, so when several concurrent calls present the same state **only one**
gets it. A replayed callback cannot pass a second time.

| Situation | Behavior |
|------|------|
| Missing, already consumed, or already evicted by the backend TTL | `InvalidStateError` |
| Retrieved but past its `expires_at` | `StateExpiredError` (the entry has been deleted too, nothing is left behind) |
| Retrieved but the content is not valid `StateData` JSON | `StateDataError` (the entry has been deleted too) |
| Otherwise | Returns `StateData` |

In the common case the backend TTL removes an expired state first, so an expired state
usually shows up as `InvalidStateError` instead of `StateExpiredError`; catch both. On
Redis and Memcached, a stored value that cannot be decoded into a cache entry at all is
treated as a miss by the backend, which also surfaces as `InvalidStateError`.

### `validate_state(state) -> bool`

Read-only check that does not consume the state: returns `True` when the state exists,
can be parsed and has not expired, otherwise `False`. It does not raise state exceptions.

> [!WARNING]
> `validate_state()` does **not** consume the state, so on its own it does not prevent replay.
> The real protection is `consume_state()`. Use `validate_state()` only for non-security
> decisions such as "probe first, then decide what UI to show".

### `get_state_metadata(state) -> dict | None`

Also non-consuming. Returns the `metadata` stored at creation, or `None` when the state
is missing, expired or cannot be parsed.

### `delete_state(state) -> bool`

Deletes a state manually (for example, when the user cancels the authorization). Returns
whether the state existed. It uses the same atomic `get_and_delete()`, so it returns `True`
to at most one caller even when racing with `consume_state()`.

## StateData

```python
class StateData(BaseModel):
    state: str  # the state string itself
    created_at: datetime  # creation time (UTC)
    expires_at: datetime  # expiry time (UTC)
    metadata: dict[str, Any]  # metadata attached at creation
```

`expires_at` is a logical expiry stored inside the data, independent of the backend TTL.
When the backend TTL runs out, the entry disappears. `expires_at` makes sure an entry the
backend still holds but that is logically expired is rejected as well.

## Dependency injection and proxy

```python
from fastapi_cachex.state import StateManagerDep, StateManagerProxy, get_state_manager


# 1. Use the type annotation directly (most common)
@app.get("/login")
async def login(states: StateManagerDep): ...


# 2. Custom instance (e.g. a different prefix or TTL): register it at startup
#    and dependency injection will return it
StateManagerProxy.set(StateManager(key_prefix="csrf:", default_ttl=300))
```

When no instance has been registered, `get_state_manager()` (the dependency behind
`StateManagerDep`) lazily creates a default `StateManager` on first use, backed by
`BackendProxy`'s backend, and registers it. It does not fall back to a `MemoryBackend`:
if no backend has been set, the request fails with `BackendNotFoundError`.

## Exceptions

```
CacheXError
└── StateError
    ├── InvalidStateError   # missing or already consumed
    ├── StateExpiredError   # expired
    └── StateDataError      # malformed content
```

## Notes

- **The backend must be shared across processes.** For multi-worker deployments use Redis or
  Memcached. With `MemoryBackend` a state only exists in the process that created it, so an
  authorization callback that lands on a different worker fails.
- **The one-time guarantee comes from the backend's atomic operation.** `get_and_delete()` is
  `GETDEL` on Redis (requires Redis server 6.2 or newer), a get followed by
  `delete(noreply=False)` where only the caller whose delete succeeded wins on Memcached,
  and a `pop` under the lock on the memory backend.
  A custom backend that implements only the abstract methods falls back to
  `BaseCacheBackend`'s non-atomic version, so a concurrent replay could succeed on both
  sides. Override `get_and_delete()` in that case.
- Do not store sensitive data in a state. `metadata` is stored as plain-text JSON in the
  cache backend.
