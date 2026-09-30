# FastAPI-Cache X {#fastapi-cache-x}

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml/badge.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml)
[![Coverage Status](https://raw.githubusercontent.com/allen0099/FastAPI-CacheX/coverage-badge/coverage.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/coverage.yml)

[![Downloads](https://static.pepy.tech/badge/fastapi-cachex)](https://pepy.tech/projects/fastapi-cachex)
[![Weekly downloads](https://static.pepy.tech/badge/fastapi-cachex/week)](https://pepy.tech/projects/fastapi-cachex)
[![Monthly downloads](https://static.pepy.tech/badge/fastapi-cachex/month)](https://pepy.tech/projects/fastapi-cachex)

[![PyPI version](https://img.shields.io/pypi/v/fastapi-cachex.svg?logo=pypi&logoColor=gold&label=PyPI)](https://pypi.org/project/fastapi-cachex)
[![Python Versions](https://img.shields.io/pypi/pyversions/fastapi-cachex.svg?logo=python&label=Python&logoColor=gold)](https://pypi.org/project/fastapi-cachex/)

[English](https://fastapi-cachex.readthedocs.io/en/latest/) | [繁體中文](https://fastapi-cachex.readthedocs.io/zh-tw/latest/)

FastAPI-CacheX 是 FastAPI 的高效能快取擴充套件：提供支援 `Cache-Control` 與 `ETag` 的伺服器端回應快取、應用層快取，以及可選的 Session 管理。

**文件：** <https://fastapi-cachex.readthedocs.io/zh-tw/latest/>（尚未翻譯的頁面與完整 API 參考請見[英文文件](https://fastapi-cachex.readthedocs.io/en/latest/)）

## 功能特點 {#features}

- **HTTP 快取**：GET 路由專用的 `@cache` 裝飾器，支援 `Cache-Control`、`ETag` / `If-None-Match`（304）與單一路由的快取失效。
- **應用層快取**：`CacheManager` 可在自己的程式碼中快取任意 JSON 值，提供未命中時才計算的 `get_or_set()` 與原子性的「不存在才寫入」`add()`。
- **後端**：記憶體、Redis 與 Memcached，支援原子操作的計數器、一次性取值與鎖。
- **Session（可選）**：以 HMAC 簽章或 JWT 發行的 Session 權杖，可經由標頭、Bearer 權杖或 Cookie 傳遞，支援滑動過期與 IP / User-Agent 綁定。
- **OAuth state**：OAuth / OIDC 流程中用於防範 CSRF 的一次性 state 權杖。

## 安裝 {#installation}

```bash
uv add fastapi-cachex
```

核心套件搭配記憶體後端即可使用；其他後端與 Session 的選用傳輸方式以 extra 提供：

| Extra | 安裝 | 帶入套件 | 用途 |
|-------|------|---------|------|
| `redis` | `uv add "fastapi-cachex[redis]"` | `redis[hiredis]`、`orjson` | `AsyncRedisCacheBackend` |
| `memcached` | `uv add "fastapi-cachex[memcached]"` | `pymemcache` | `MemcachedBackend` |
| `jwt` | `uv add "fastapi-cachex[jwt]"` | `PyJWT` | `SessionConfig(token_format="jwt")` |

Extra 可以組合：`uv add "fastapi-cachex[redis,jwt]"`。

## 快速開始 {#quick-start}

```python
from fastapi import FastAPI

from fastapi_cachex import AppCache, BackendProxy, cache
from fastapi_cachex.backends import MemoryBackend

app = FastAPI()
BackendProxy.set(MemoryBackend())  # 或 AsyncRedisCacheBackend / MemcachedBackend


@app.get("/items/{item_id}")
@cache(ttl=60)  # 60 秒內直接由快取回應，並支援 ETag 重新驗證
async def read_item(item_id: int):
    return {"item_id": item_id}


def build_report() -> dict:
    return {"total": 42}  # 代表某個耗時的計算


@app.get("/report")
async def report(cache: AppCache):
    # 在自己的程式碼中快取任意 JSON 值。同時未命中時只執行一次
    # build_report：get_or_set() 預設會取鎖。
    return await cache.get_or_set("report", build_report, ttl=300)
```

> [!IMPORTANT]
> `@cache` 必須寫在路由裝飾器的**下方**。FastAPI 註冊的是傳到 `@app.get(...)` 的那個函式；若 `@cache` 寫在上方，FastAPI 註冊的是未經裝飾的 handler，路由照常運作，但什麼都不會被快取，也不會有任何警告（詳見 [裝飾器順序](HTTP_CACHING.md#decorator-order)）。
>
> ```python
> @app.get("/items")  # ✅ 先寫路由裝飾器，
> @cache(ttl=60)      #    @cache 緊貼在函式上方
> async def items(): ...
>
> @cache(ttl=60)      # ❌ 永遠不會被呼叫：什麼都不會被快取
> @app.get("/items")
> async def items(): ...
> ```

> [!WARNING]
> 預設的快取鍵不包含使用者身分。需要驗證身分的端點請使用 `private=True`，或依使用者區分的 key builder 搭配 `cache_authorized=True`（否則帶有 `Authorization` 或 Session 的請求會繞過後端），詳見 [需驗證身分的端點](HTTP_CACHING.md#authenticated-endpoints)。

## 文件 {#documentation}

- [遷移至 0.4.0](MIGRATING_0_4.md)：0.4.0 的變更，以及現在如何處理 0.3.9 的警告
- [HTTP 快取](HTTP_CACHING.md)：`@cache` 裝飾器、Cache-Control 指令、快取鍵、快取失效與監控路由
- [應用層快取](APP_CACHE.md)：`CacheManager`
- [後端](BACKENDS.md)：選擇與設定後端、原子操作的基本功能
- [分散式鎖](LOCK.md)：以 `CacheLock` 在多個行程之間互斥
- [Session 管理](SESSION.md)、[OAuth state](STATE.md)（一次性的 OAuth / CSRF state 權杖）與 [JWT claims](JWT_CLAIMS.md)
- [快取流程](CACHE_FLOW.md)：快取請求內部的處理流程
- [可執行範例](https://github.com/allen0099/FastAPI-CacheX/tree/master/examples)（英文）：每個功能一個完整的應用程式，皆由測試套件涵蓋
- [API 參考](https://fastapi-cachex.readthedocs.io/en/latest/api/http-caching/)（英文）
- [開發指南](https://fastapi-cachex.readthedocs.io/en/latest/DEVELOPMENT/)（英文）與[貢獻指南](CONTRIBUTING.md) · [安全性政策](https://github.com/allen0099/FastAPI-CacheX/blob/master/SECURITY.md)（英文）：請私下回報漏洞，不要開公開 issue
- [變更紀錄](https://github.com/allen0099/FastAPI-CacheX/blob/master/CHANGELOG.md)（英文） · [已知限制與規劃中的工作](https://github.com/allen0099/FastAPI-CacheX/issues)

## 授權 {#license}

本專案採用 Apache License 2.0 授權，詳見 [LICENSE](https://github.com/allen0099/FastAPI-CacheX/blob/master/LICENSE)。
