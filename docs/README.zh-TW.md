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

FastAPI-CacheX 是一個為 FastAPI 框架設計的高效能快取擴充套件，提供完整的 HTTP 快取功能支援。

## 功能特點

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

### 使用 pip 安裝

```bash
pip install fastapi-cachex
```

### 使用 uv 安裝（推薦）

```bash
uv pip install fastapi-cachex
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

### 記憶體快取 (預設後端)

```python
from fastapi_cachex.backends import MemoryBackend
from fastapi_cachex import BackendProxy

backend = MemoryBackend()
BackendProxy.set_backend(backend)
```

### Memcached

```python
from fastapi_cachex.backends import MemcachedBackend
from fastapi_cachex import BackendProxy

backend = MemcachedBackend(servers=["localhost:11211"])
BackendProxy.set_backend(backend)
```

### Redis

```python
from fastapi_cachex.backends import AsyncRedisCacheBackend
from fastapi_cachex import BackendProxy

backend = AsyncRedisCacheBackend(host="127.0.1", port=6379, db=0)
BackendProxy.set_backend(backend)
```

## 文件

- [開發指南](DEVELOPMENT.md)
- [貢獻指南](CONTRIBUTING.md)

## 授權條款

本專案採用 Apache License 2.0 授權條款 - 查看 [LICENSE](../LICENSE) 文件了解更多細節。
