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

[English](README.md) | [繁體中文](docs/README.zh-TW.md)

A high-performance caching extension for FastAPI, providing comprehensive HTTP caching support.

## Features

- Support for HTTP caching headers
    - `Cache-Control`
    - `ETag`
    - `If-None-Match`
- Multiple backend cache support
    - Redis
    - Memcached
    - In-memory cache
- Complete Cache-Control directive implementation
- Easy-to-use `@cache` decorator

### Cache-Control Directives

| Directive                | Supported          | Description                                                                                             |
|--------------------------|--------------------|---------------------------------------------------------------------------------------------------------|
| `max-age`                | :white_check_mark: | Specifies the maximum amount of time a resource is considered fresh.                                    |
| `s-maxage`               | :x:                | Specifies the maximum amount of time a resource is considered fresh for shared caches.                  |
| `no-cache`               | :white_check_mark: | Forces caches to submit the request to the origin server for validation before releasing a cached copy. |
| `no-store`               | :white_check_mark: | Instructs caches not to store any part of the request or response.                                      |
| `no-transform`           | :x:                | Instructs caches not to transform the response content.                                                 |
| `must-revalidate`        | :white_check_mark: | Forces caches to revalidate the response with the origin server after it becomes stale.                 |
| `proxy-revalidate`       | :x:                | Similar to `must-revalidate`, but only for shared caches.                                               |
| `must-understand`        | :x:                | Indicates that the recipient must understand the directive or treat it as an error.                     |
| `private`                | :white_check_mark: | Indicates that the response is intended for a single user and should not be stored by shared caches.    |
| `public`                 | :white_check_mark: | Indicates that the response may be cached by any cache, even if it is normally non-cacheable.           |
| `immutable`              | :white_check_mark: | Indicates that the response body will not change over time, allowing for longer caching.                |
| `stale-while-revalidate` | :white_check_mark: | Indicates that a cache can serve a stale response while it revalidates the response in the background.  |
| `stale-if-error`         | :white_check_mark: | Indicates that a cache can serve a stale response if the origin server is unavailable.                  |

## Installation

### Using pip

```bash
pip install fastapi-cachex
```

### Using uv (recommended)

```bash
uv pip install fastapi-cachex
```

## Quick Start

```python
from fastapi import FastAPI
from fastapi_cachex import cache
from fastapi_cachex import CacheBackend

app = FastAPI()


@app.get("/")
@cache(ttl=60)  # Cache for 60 seconds
async def read_root():
    return {"Hello": "World"}


@app.get("/no-cache")
@cache(no_cache=True)  # Mark this endpoint as non-cacheable
async def non_cache_endpoint():
    return {"Hello": "World"}


@app.get("/no-store")
@cache(no_store=True)  # Mark this endpoint as non-cacheable
async def non_store_endpoint():
    return {"Hello": "World"}


@app.get("/clear_cache")
async def remove_cache(cache: CacheBackend):
    await cache.clear_path("/path/to/clear")  # Clear cache for a specific path
    await cache.clear_pattern("/path/to/clear/*")  # Clear cache for a specific pattern
```

## Backend Configuration

FastAPI-CacheX supports multiple caching backends. You can easily switch between them using the `BackendProxy`.

### In-Memory Cache (default)

If you don't specify a backend, FastAPI-CacheX will use the in-memory cache by default.
This is suitable for development and testing purposes.

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

## Documentation

- [Development Guide](docs/DEVELOPMENT.md)
- [Contributing Guidelines](docs/CONTRIBUTING.md)

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
