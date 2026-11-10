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
