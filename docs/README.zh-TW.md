# FastAPI-Cache X

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml/badge.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml)
[![Coverage Status](https://raw.githubusercontent.com/allen0099/FastAPI-CacheX/coverage-badge/coverage.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/coverage.yml)

[![Downloads](https://static.pepy.tech/badge/fastapi-cachex)](https://pepy.tech/project/fastapi-cachex)
[![Weekly downloads](https://static.pepy.tech/badge/fastapi-cachex/week)](https://pepy.tech/project/fastapi-cachex)
[![Monthly downloads](https://static.pepy.tech/badge/fastapi-cachex/month)](https://pepy.tech/project/fastapi-cachex)

[![PyPI version](https://img.shields.io/pypi/v/fastapi-cachex.svg?logo=pypi&logoColor=gold&label=PyPI)](https://pypi.org/project/fastapi-cachex)
[![Python Versions](https://img.shields.io/pypi/pyversions/fastapi-cachex.svg?logo=python&label=Python&logoColor=gold)](https://pypi.org/project/fastapi-cachex/)

[English](../README.md) | [繁體中文](README.zh-TW.md)

FastAPI-CacheX 是一個為 FastAPI 框架設計的高效能快取擴充套件，提供完整的 HTTP 快取功能支援和可選的 Session 管理。

## 功能特點

### HTTP 快取
- 支援多種 HTTP 快取標頭
    - `Cache-Control`
    - `ETag`
    - `If-None-Match`
- 支援多種後端快取系統
    - Redis
    - Memcached
    - 記憶體內快取
- 完整實現 Cache-Control 指令
- 簡單易用的 `@cache` 裝飾器

### Session 管理（可選擴充套件）
- 使用 HMAC-SHA256 權杖簽名的安全 Session 管理
- IP 地址和 User-Agent 綁定（可選安全功能）
- Header 和 Bearer 權杖支援（API-first 架構）
- 自動 Session 更新（滑動過期）
- 跨請求通訊的 Flash Messages
- 支援多種後端（Redis、Memcached、記憶體內）
- 完整的 Session 生命週期管理（建立、驗證、更新、失效）

### Cache-Control 指令

| 指令                       | 支援狀態               | 說明                              |
|--------------------------|--------------------|---------------------------------|
| `max-age`                | :white_check_mark: | 指定資源被認為是新鮮的最長時間。                |
| `s-maxage`               | :x:                | 在共享快取中資源被認為是新鮮的最長時間。            |
| `no-cache`               | :white_check_mark: | 強制快取在釋放快取副本前向原始伺服器驗證請求。         |
| `no-store`               | :white_check_mark: | 指示快取不儲存請求或回應的任何部分。              |
| `no-transform`           | :x:                | 指示快取不要轉換回應內容。                   |
| `must-revalidate`        | :white_check_mark: | 強制快取在資源過期後向原始伺服器重新驗證。           |
| `proxy-revalidate`       | :x:                | 類似 `must-revalidate`，但僅適用於共享快取。 |
| `must-understand`        | :x:                | 表示接收者必須理解該指令，否則應視為錯誤。           |
| `private`                | :white_check_mark: | 表示回應僅供單一使用者使用，不應被共享快取儲存。        |
| `public`                 | :white_check_mark: | 表示回應可被任何快取儲存，即使通常是無法快取的。        |
| `immutable`              | :white_check_mark: | 表示回應內容不會隨時間改變，允許更長時間的快取。        |
| `stale-while-revalidate` | :white_check_mark: | 表示快取可以在背景重新驗證時提供過期的回應。          |
| `stale-if-error`         | :white_check_mark: | 表示當原始伺服器無法訪問時，快取可以提供過期的回應。      |

## 安裝指南

```bash
uv pip install fastapi-cachex
```

### 開發版本安裝

```bash
uv add git+https://github.com/allen0099/FastAPI-CacheX.git
```

## 快速開始

```python
from fastapi import FastAPI
from fastapi_cachex import cache
from fastapi_cachex import CacheBackend

app = FastAPI()


@app.get("/")
@cache(ttl=60)  # 快取 60 秒
async def read_root():
    return {"Hello": "World"}


@app.get("/no-cache")
@cache(no_cache=True)  # 標記此端點為不可快取
async def non_cache_endpoint():
    return {"Hello": "World"}


@app.get("/no-store")
@cache(no_store=True)  # 標記此端點為不可儲存
async def non_store_endpoint():
    return {"Hello": "World"}


@app.get("/clear_cache")
async def remove_cache(cache: CacheBackend):
    await cache.clear_path("/path/to/clear")  # 清除特定路徑的快取
    await cache.clear_pattern("/path/to/clear/*")  # 清除符合特定模式的快取
```

## 後端設定

FastAPI-CacheX 支援多種快取後端。你可以使用 `BackendProxy` 輕鬆切換不同的後端。

### 快取金鑰格式

快取金鑰遵循以下格式以避免衝突：

```
{method}|||{host}|||{path}|||{query_params}
```

這確保：
- 不同的 HTTP 方法 (GET、POST 等) 不會共享快取
- 不同的主機不會共享快取（適合多租戶情境）
- 不同的查詢參數將獲得各自獨立的快取項目
- 同一端點配合不同參數可以獨立快取

所有後端會自動為金鑰加上前綴（例如 `fastapi_cachex:`）以避免與其他應用程式衝突。

### 快取命中行為

當快取項目有效（在 TTL 內）時：
- **預設行為**：直接返回快取內容並使用 HTTP 200 狀態碼，無需重新執行端點處理器
- **帶有 `If-None-Match` 標頭**：如果 ETag 匹配則返回 HTTP 304 Not Modified
- **帶有 `no-cache` 指令**：強制重新驗證以確定是否需要返回 304

這意味著**快取命中非常快速** - 端點處理器函數永遠不會被執行。

### 記憶體快取 (預設後端)

若未指定後端，FastAPI-CacheX 預設使用記憶體快取。
適合開發和測試。後端會自動執行清理任務，每 60 秒移除過期項目。

```python
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex import BackendProxy

backend = MemoryBackend()
BackendProxy.set_backend(backend)
```

**注意**：記憶體快取不適合多處理程式的生產環境。
每個處理程式維護其自己獨立的快取。

### Memcached

```python
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex import BackendProxy

backend = MemcachedBackend(servers=["localhost:11211"])
BackendProxy.set_backend(backend)
```

**限制**：
- Memcached 協議不支援基於模式的金鑰清除 (`clear_pattern`)
- 金鑰使用 `fastapi_cachex:` 前綴以避免衝突
- 如果需要基於模式的快取清除，請考慮使用 Redis 後端

### Redis

```python
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex import BackendProxy

backend = AsyncRedisCacheBackend(host="127.0.0.1", port=6379, db=0)
BackendProxy.set_backend(backend)
```

**功能**：
- 完全非同步實現
- 支援基於模式的金鑰清除
- 使用 SCAN 而非 KEYS 以保證生產環境安全（非阻塞）
- 預設使用 `fastapi_cachex:` 前綴
- 支援自訂金鑰前綴用於多租戶情境

**自訂前綴範例**：

```python
backend = AsyncRedisCacheBackend(
    host="127.0.0.1",
    port=6379,
    key_prefix="myapp:cache:",
)
BackendProxy.set_backend(backend)
```

## 效能考量

### 快取命中效能

當快取命中（在 TTL 內）時，回應會直接返回而無需執行端點處理器。這非常快速：

```python
@app.get("/expensive")
@cache(ttl=3600)  # 快取 1 小時
async def expensive_operation():
    # 只在快取未命中時執行
    # 快取命中時，此函數不會被呼叫
    result = perform_expensive_calculation()
    return result
```

### 後端選擇

- **MemoryBackend**：單處理程式開發時最快；不適合生產環境
- **Memcached**：適合分散式系統；模式清除有限制
- **Redis**：最適合生產環境；完全非同步、支援所有功能、非阻塞操作

## 文件

- [快取流程說明](CACHE_FLOW.zh-TW.md)
- [開發指南](DEVELOPMENT.md)
- [貢獻指南](CONTRIBUTING.md)
- [Session 管理指南](SESSION.md) - 完整的 Session 功能使用指南

## 授權條款

本專案採用 Apache License 2.0 授權條款 - 查看 [LICENSE](../LICENSE) 文件了解更多細節。
