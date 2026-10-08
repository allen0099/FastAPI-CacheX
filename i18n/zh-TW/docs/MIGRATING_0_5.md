# 遷移至 0.5.0 {#migrating-to-050}

0.5.0 包含破壞性變更，收錄於 [0.5.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.5.0)。本頁列出每一項變更、需要修改的地方，以及 0.4.x 是否已經會發出警告。其中最大的一項是移除 0.4.0 已棄用的 Session 與 OAuth state。

## 升級之前 {#before-you-upgrade}

請先升級到最新的 0.4.x 版本，並在把本函式庫的警告轉為錯誤的情況下執行測試：

```bash
python -W error::FutureWarning -m pytest
```

或使用 pytest 本身的設定：

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error::FutureWarning",
]
```

匯入 `fastapi_cachex.session` 或 `fastapi_cachex.state` 時，以及第三方後端的 `delete()` 回傳 `None` 時，0.4.x 會發出 `FutureWarning`。0.4.x 不再發出這些警告之後，「0.4.x 是否警告」欄位中有警告的變更就已處理完畢；本頁其餘部分說明警告偵測不到的變更。

## 摘要 {#summary}

| 變更 | Issue | 0.4.x 是否警告 | 章節 |
|------|-------|----------------|------|
| 移除 Session 與 OAuth state | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | `FutureWarning` | [Session 與 OAuth state](#session-state-removed) |
| 移除 `jwt` extra | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | 否 | [jwt extra](#jwt-extra) |
| `@cache` 不再辨識 Session 中介軟體與 `X-Session-Token` | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | 否 | [Session 權杖與 @cache](#cache-session-token) |
| `itsdangerous` 與 `starlette` 不再是直接依賴；`fastapi` 的最低版本降低 | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | 否 | [依賴套件](#dependencies) |
| 後端 `delete()` 回傳 `None` 時視為未移除 | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | `FutureWarning` | [delete() 回傳 None](#backend-delete-none) |

## Session 與 OAuth state 已移除 {#session-state-removed}

0.4.0 已棄用（[#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)）的 `fastapi_cachex.session` 與 `fastapi_cachex.state` 已經移除（[#421](https://github.com/allen0099/FastAPI-CacheX/issues/421)）。一併移除的還有 `FastAPICacheXSessionMiddleware`、`SessionManager`、`SessionConfig`、Session 依賴項（`AuthenticatedSession`、`OptionalSession`、`get_session` 等）、權杖序列化器、`StateManager`，以及它們的 proxy 與例外。現在匯入其中任一個套件會拋出 `ModuleNotFoundError`，從 `fastapi_cachex` 讀取這些名稱（例如 `fastapi_cachex.SessionConfig`）則會拋出 `AttributeError`；兩者都不再先發出警告。

遷移方向與 0.4.0 相同：請參閱 0.4.0 遷移指南中的 [Session 與 OAuth state 已棄用](MIGRATING_0_4.md#session-state-deprecated)。簡單來說，簽署 Cookie 的 Session 改用 Starlette 的 `SessionMiddleware`，必須存放在伺服器端的 Session 改用伺服器端 Session 函式庫，API 改用你的身分驗證架構所發出的存取權杖，OAuth 的 `state` 則交給你的 OAuth 用戶端函式庫。`@cache`、`CacheManager`、`CacheLock` 與各後端都不受影響。

已存在後端中的 Session 與 state 不需要清理，它們會依自己的 TTL 過期。若要在 Redis 或記憶體後端上立即移除，請清除它們的前綴（預設為 `session:` 與 `oauth_state:`）：

```python
await backend.clear_pattern("session:*")
await backend.clear_pattern("oauth_state:*")
```

請先確認你自己的鍵沒有以這兩個前綴開頭。Memcached 無法列舉鍵，因此只能等它們過期。

已移除套件的 0.4.x 文件仍可在儲存庫的 [v0.4.1 標籤](https://github.com/allen0099/FastAPI-CacheX/tree/v0.4.1/i18n/zh-TW/docs)中閱讀。

### jwt extra {#jwt-extra}

`jwt` extra 只是為了 `SessionConfig(token_format="jwt")` 安裝 `PyJWT`，因此一併移除。`uv add "fastapi-cachex[jwt]"`（或 pip）現在只會對未知的 extra 發出警告，並在不安裝 `PyJWT` 的情況下完成安裝。若你自己的程式碼使用 `PyJWT`，請直接依賴它：

```bash
# Before
uv add "fastapi-cachex[redis,jwt]"

# After
uv add "fastapi-cachex[redis]" PyJWT
```

## Session 權杖與 @cache {#cache-session-token}

對於帶有憑證的請求，`@cache` 會繞過共用後端，並以 `private` 送出回應（見 [HTTP 快取](HTTP_CACHING.md#authenticated-endpoints)）。現在只剩兩種憑證：

- `Authorization` 標頭；
- 不是空的 `request.session`，來自任何 Session 中介軟體，例如 Starlette 的 `SessionMiddleware`。

0.4.x 另外會計入、0.5.0 不再計入的：

- `FastAPICacheXSessionMiddleware` 載入的 Session（來自它的 `X-Session-Token` 標頭、Bearer 權杖或它的 Cookie），即使其中沒有資料也算。中介軟體已移除，這項檢查也隨之移除。
- `@cache(vary=[...])` 中的 `X-Session-Token` 會像 `Authorization`、`Proxy-Authorization` 與 `Cookie` 一樣，以值的 SHA-256 摘要作為鍵。現在它是一般的標頭，值會原樣出現在鍵中。

單獨的 `Cookie` 標頭仍然不會繞過快取，與以前相同。若取代 Session 中介軟體的做法以 `@cache` 看不到的標頭或 Cookie 辨識呼叫者，只加上 `@cache` 的路由會把第一位呼叫者的回應提供給所有人。請使用 `private=True`，或讓 `key_builder` 把已驗證的身分放進鍵中並搭配 `cache_authorized=True`；若是自訂的權杖標頭，請透過 `key_builder`（自行雜湊）而不是 `vary` 以它作為鍵：

```python
# Before: X-Session-Token was hashed, and a loaded session bypassed the backend
@cache(ttl=60, vary=["X-Session-Token"], cache_authorized=True)

# After: hash the token yourself, or better, key on the verified user id
def per_user_key(request: Request) -> str:
    return build_cache_key(request, request.state.user_id)


@cache(ttl=60, key_builder=per_user_key, cache_authorized=True)
```

0.4.x 不會警告：它無法得知你會用什麼取代 Session 中介軟體。

## 依賴套件 {#dependencies}

核心需要的套件變少，版本也更舊（[#421](https://github.com/allen0099/FastAPI-CacheX/issues/421)）：

- `itsdangerous` 不再是必要依賴，只有 Session 中介軟體需要它。若你改用會匯入它的 Starlette `SessionMiddleware`，請把 `itsdangerous` 加入你自己的依賴。
- `starlette` 不再是直接依賴。0.4.x 為了 Session 中介軟體要求 `starlette>=1.0.0`；0.5.0 使用你的 `fastapi` 所允許的任何版本。
- `fastapi` 的最低版本從 `0.133.0` 降到 `0.128.2`，這是測試套件（搭配它接受的最舊 Starlette 0.40.0）能通過的最舊版本。`pydantic>=2.7.0` 不變。

除非專案中有其他部分依靠 `fastapi-cachex` 安裝 `itsdangerous` 或較新的 `starlette`，否則不需要任何修改；若有，請自行指定這些套件。

## delete() 回傳 None {#backend-delete-none}

0.4.0 起 `BaseCacheBackend.delete()` 會回傳是否移除了鍵（見 [delete() 的回傳值](MIGRATING_0_4.md#backend-delete)）。基底類別上的非原子性後備實作（`delete_many()`、`get_and_delete()` 與 `delete_if_equals()`）仍會像 0.3.x 一樣把第三方 `delete()` 回傳的 `None` 視為已移除，並發出 `FutureWarning`。0.5.0 以 `bool()` 讀取結果，因此 `None` 現在視為未移除，也不再警告：此時 `delete_many()` 回傳 `0`，`get_and_delete()` 與 `delete_if_equals()` 則回報呼叫者沒有成功，即使鍵已經不在了。`CacheManager.delete()` 建立在 `get_and_delete()` 之上，因此也會回傳 `False`。

內建的後端不受影響。第三方後端只要回傳 `bool` 即可：

```python
# Before
class MyBackend(BaseCacheBackend):
    async def delete(self, key: str) -> None:
        await self._client.delete(key)


# After
class MyBackend(BaseCacheBackend):
    async def delete(self, key: str) -> bool:
        return await self._client.delete(key) > 0
```

每當後備實作收到 `None`，0.4.x 都會發出 `FutureWarning`。
