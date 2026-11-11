# 遷移至 0.6.0 {#migrating-to-060}

0.6.0 尚未發佈。它的破壞性變更收錄於 [0.6.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.6.0)，本頁會隨變更合併而增加內容。

## Python 3.11 或更新版本 {#python-311}

Python 3.10 已在 2026 年 10 月結束生命週期（end of life），0.6.0 需要 Python 3.11 或更新版本（[#491](https://github.com/allen0099/FastAPI-CacheX/issues/491)）。0.5.2 起的 0.5.x 版本會先預告：在 Python 3.10 上匯入 `fastapi_cachex` 時，每個行程會發出一次 `FutureWarning`：

```text
FutureWarning: fastapi-cachex 0.6.0 will require Python 3.11 or later; ...
```

在 Python 3.10 上，不會有東西自行壞掉。`pip` 與 `uv` 會讀取 `requires-python`，繼續安裝最新的 0.5.x。但版本只從 `master` 發佈，所以第一個 0.6.0 變更合併之後，0.5.x 就不再發佈新版本，安全修補也包括在內，這可能遠早於 0.6.0 本身發佈的時間（見[安全政策](https://github.com/allen0099/FastAPI-CacheX/blob/master/SECURITY.md)）。因此真正的解法是改用 Python 3.11 或更新版本。

這個警告來自匯入本身，而不是你的程式碼呼叫了什麼，所以修改程式碼無法消除它。把警告轉為錯誤的測試（`filterwarnings = ["error"]`）在 Python 3.10 上會失敗，直到你升級 Python，或忽略這一個警告：

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore:fastapi-cachex 0.6.0 will require Python 3.11:FutureWarning",
]
```

在 pytest 之外要關閉它，請在第一次 `import fastapi_cachex` 之前設定過濾規則：

```python
import warnings

warnings.filterwarnings(
    "ignore",
    message="fastapi-cachex 0.6.0 will require Python 3.11",
    category=FutureWarning,
)

import fastapi_cachex  # noqa: E402
```

或在環境中設定 `PYTHONWARNINGS="ignore:fastapi-cachex 0.6.0 will require Python 3.11:FutureWarning"`。

`redis` extra 現在需要 `orjson` 3.8.0 或更新版本（原本是 3.4.7）：3.8.0 是第一個在 Linux、macOS 與 Windows 上都提供 Python 3.11 wheel 的版本。若你的環境將 `orjson` 固定在較舊的版本，需要調高。

## Memcached 逾時預設為 1 秒 {#memcached-timeouts}

`MemcachedBackend` 原本在未指定時，建立連線與每次等待回應都會等 5 秒。0.6.0 的這兩個預設值為 1 秒，與 Redis 後端一致（[#516](https://github.com/allen0099/FastAPI-CacheX/issues/516)）：每次 Memcached 呼叫都會佔用一個工作執行緒，直到伺服器回應或逾時，因此過長的預設值會讓無法連線的伺服器拖住同步 handler 與依賴項共用的執行緒池。

0.5.3 起的 0.5.x 版本會先預告：在這些版本建立後端時若沒有同時傳入這兩個參數，會發出 `FutureWarning`：

```text
FutureWarning: MemcachedBackend timeouts will default to 1 second in fastapi-cachex 0.6.0; ...
```

在 0.5.x 上，同時傳入兩者以選定數值並讓警告消失。在 0.6.0 上，只有需要 1 秒以外的數值（例如原本的 5 秒）時才需要傳入：

```python
backend = MemcachedBackend(
    servers=["localhost:11211"],
    connect_timeout=1.0,  # 或 5.0，即 0.6.0 之前的預設值
    timeout=1.0,
)
```

0.5.x 把 `None` 視為「未傳入」，0.6.0 則會對它引發 `TypeError`；請改為省略該參數。

把警告視為錯誤的測試套件（`filterwarnings = ["error"]`）在這些 0.5.x 版本上，只要有地方未同時傳入這兩個參數就建立後端，就會失敗。若後端是由你無法修改的程式碼建立的，請改為忽略這一則警告：

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore:MemcachedBackend timeouts will default to 1 second:FutureWarning",
]
```

在 pytest 之外，於建立後端之前呼叫 `warnings.filterwarnings("ignore", message="MemcachedBackend timeouts will default to 1 second", category=FutureWarning)` 有相同效果，在環境變數中設定 `PYTHONWARNINGS="ignore:MemcachedBackend timeouts will default to 1 second:FutureWarning"` 也是。

對 `@cache` 而言，逾時與其他後端失敗相同：請求會[在不使用快取的情況下回應](HTTP_CACHING.md#when-the-backend-fails)。若伺服器經常需要超過一秒才回應，請明確寫出較大的數值。

## `@cached` 會驗證成回傳型別註記的型別 {#cached-return-annotation}

以 `@cached` 裝飾、且未傳入 `type_=` 的函式，過去回傳的是值的 JSON 形式：標註 `-> User` 的拿回 `dict`，標註 `-> tuple[int, int]` 的拿回 list，第一次呼叫和之後每次命中都一樣。在 0.6.0 中，未傳入 `type_` 時，`@cached` 會把快取的值驗證成函式的回傳型別註記（[#533](https://github.com/allen0099/FastAPI-CacheX/issues/533)），因此這些函式會回傳 `User` 與 tuple。

0.5.6 與之後的 0.5.x 版本會在套用裝飾器的地方以 `FutureWarning` 預告這項變更：

```text
FutureWarning: cached return annotation: load_user() is annotated to return <class 'app.User'>, and from 0.6.0 @cached validates the cached value into that type ...
```

以下情況沒有任何改變，先前也不會收到警告：

- 有傳入 `type_`，不論值是什麼；
- 函式沒有回傳型別註記；
- 註記只由 JSON 自己的型別組成：`str`、`int`、`float`、`bool`、`None`、`Any`、`object`、`list` 與 `dict`（不帶參數，或寫成 `list[...]`、`dict[str, ...]`），以及它們的 union。這類值會照 JSON 解碼的結果回傳，不會被轉換。把這類型別包在 `Annotated[...]` 裡也不會改變任何事：其中的中繼資料不會套用，所以 `-> Annotated[int, Field(gt=0)]` 不會被檢查。要檢查的話請以 `type_=` 傳入。

唯一需要特別留意的情況，是在套用裝飾器的地方無法解析的回傳型別註記。這發生在型別是放在 `if TYPE_CHECKING:` 底下匯入（linter 常會把匯入搬到那裡），或是檔案後面才定義的類別時。`@cached` 讀不到這種註記，所以該函式會繼續回傳 JSON 形式，而套用裝飾器時會發出以 `cached return annotation` 開頭的 `UserWarning`（0.5.6 是以相同前綴的 `FutureWarning` 預告）。傳入 `type_=<該型別>` 可取得該型別，傳入 `type_=None` 則維持 JSON 形式且不再警告。

pydantic 無法驗證的回傳型別註記也會得到相同的 `UserWarning` 與 JSON 形式：`Iterator[int]`、`Self`、`NoReturn`、`Response`、protocol 或一般類別。若驗證成這類型別，每次呼叫都會拋出例外。

若要讓某個函式維持 JSON 形式，請明確寫出：

```python
@cached(ttl=60, type_=None)
async def load_user_dict(user_id: int) -> User: ...
```

升級時請檢查：

- 把結果當成 `dict` 使用的程式碼（`user["id"]`）現在會拿到模型（`user.id`）。
- 0.5.x 快取下來、但不符合註記的值會視為未命中，函式會重新執行，因此升級時不需要清空快取。
- 若函式回傳的值不符合它的註記，呼叫會拋出 pydantic 的 `ValidationError`；過去這種不一致不會被發現。
- 值是從它的 JSON 形式重建的，因此無法接受自身 JSON 的模型每次呼叫都會拋出 `ValidationError`。這包括在 `extra="forbid"` 模型上的 `computed_field`，以及設了 `Field(exclude=True)` 的必填欄位。這類函式請使用 `type_=None`。
- 結果就是註記的型別本身。註記為 `-> User` 卻回傳 `User` 子類別的函式會拿回 `User`，子類別多出的欄位不會保留；`-> Cat | Dog` 則由 pydantic 挑選 JSON 符合的成員，因此長得一樣的模型請加上 discriminator。
- `-> Iterable[int]` 會得到只能走訪一次的 iterator，而不是 list。請註記為 `list[int]`。
- 每次呼叫都會執行驗證，不論命中與否。對於非常頻繁讀取的大型值，`type_=None` 可以避免這項成本。

## `/cached-hits` 不再有 `*_hits` 欄位 {#cached-hits-fields}

監控路由 `GET {prefix}/cached-hits`（見 `add_routes()`）原本把每個值回報兩次：一次放在 `entries`、`total_entries`、`valid_entries` 與 `expired_entries`，另一次放在較舊的 `cached_hits`、`total_hits`、`valid_hits` 與 `expired_hits`。舊名稱讓人以為是由快取回應的請求數，實際上統計的是已儲存的項目。舊名稱在 0.5.1 已棄用，並在 0.6.0 移除（[#507](https://github.com/allen0099/FastAPI-CacheX/issues/507)）。

| 已移除 | 請改讀 |
|---|---|
| `cached_hits` | `entries` |
| `total_hits` | `total_entries` |
| `valid_hits` | `valid_entries` |
| `expired_hits` | `expired_entries` |

兩者的值相同，因此讀取這個回應的儀表板或腳本只需要改用新名稱；新名稱從 0.5.1 起就存在，所以可以在升級之前先切換。`unique_routes` 與 `summary` 不變。
