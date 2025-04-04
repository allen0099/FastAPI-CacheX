# FastAPI-Cache X

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml/badge.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml)
[![Coverage Status](https://raw.githubusercontent.com/allen0099/FastAPI-CacheX/coverage-badge/coverage.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml)

[![PyPI version](https://badge.fury.io/py/fastapi-cachex.svg)](https://badge.fury.io/py/fastapi-cachex)
[![Python Versions](https://img.shields.io/pypi/pyversions/fastapi-cachex.svg)](https://pypi.org/project/fastapi-cachex/)

[English](../README.md) | [繁體中文](README.zh-TW.md)

FastAPI-CacheX 是一個為 FastAPI 框架設計的高效能緩存擴充套件，提供完整的 HTTP 緩存功能支援。

## 功能特點

- 支援多種 HTTP 緩存標頭
  - `Cache-Control`
  - `ETag`
  - `If-None-Match`
- 支援多種後端緩存系統
  - Redis
  - Memcached
  - 記憶體內緩存
- 完整實現 Cache-Control 指令
- 簡單易用的 `@cache` 裝飾器

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

app = FastAPI()

@app.get("/")
@cache()
async def read_root():
    return {"Hello": "World"}
```

## 開發指南

### 運行測試

1. 運行單元測試：
```bash
pytest
```

2. 運行測試並產生覆蓋率報告：
```bash
pytest --cov=fastapi_cachex
```

### 使用 tox 進行測試

tox 用於確保程式碼在不同 Python 版本（3.10-3.13）中都能正常運作。

1. 安裝所有 Python 版本
2. 運行 tox：
```bash
tox
```

若要運行特定 Python 版本：
```bash
tox -e py310  # 僅運行 Python 3.10
```

## 貢獻指南

1. Fork 專案
2. 建立你的功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交你的修改 (`git commit -m 'Add some AmazingFeature'`)
4. Push 到分支 (`git push origin feature/AmazingFeature`)
5. 開啟一個 Pull Request

## 授權條款

本專案採用 Apache License 2.0 授權條款 - 查看 [LICENSE](../LICENSE) 文件了解更多細節。
