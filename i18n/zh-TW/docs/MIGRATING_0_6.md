# 遷移至 0.6.0 {#migrating-to-060}

0.6.0 尚未發佈。它的破壞性變更收錄於 [0.6.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.6.0)，本頁會隨變更合併而增加內容。

## Python 3.11 或更新版本 {#python-311}

Python 3.10 已在 2026 年 10 月結束生命週期（end of life），0.6.0 需要 Python 3.11 或更新版本（[#491](https://github.com/allen0099/FastAPI-CacheX/issues/491)）。從下一個 0.5.x 版本起，在 Python 3.10 上匯入 `fastapi_cachex` 時，每個行程會發出一次 `FutureWarning`：

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

## Memcached 逾時預設為 1 秒 {#memcached-timeouts}

`MemcachedBackend` 在未指定時，建立連線與每次等待回應都會等 5 秒。0.6.0 會把這兩個預設值改為 1 秒，與 Redis 後端一致（[#516](https://github.com/allen0099/FastAPI-CacheX/issues/516)）：每次 Memcached 呼叫都會佔用一個工作執行緒，直到伺服器回應或逾時，因此過長的預設值會讓無法連線的伺服器拖住同步 handler 與依賴項共用的執行緒池。

從 0.5.3 起，建立後端時若沒有同時傳入這兩個參數，會發出 `FutureWarning`：

```text
FutureWarning: MemcachedBackend timeouts will default to 1 second in fastapi-cachex 0.6.0; ...
```

現在就同時傳入兩者以選定數值，這也會讓警告消失：

```python
backend = MemcachedBackend(
    servers=["localhost:11211"],
    connect_timeout=1.0,  # 或 5.0 以維持目前的行為
    timeout=1.0,
)
```

對 `@cache` 而言，逾時與其他後端失敗相同：請求會[在不使用快取的情況下回應](HTTP_CACHING.md#when-the-backend-fails)。若伺服器經常需要超過一秒才回應，請明確寫出較大的數值。
