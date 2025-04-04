# FastAPI-Cache X

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml/badge.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml)
[![Coverage Status](https://raw.githubusercontent.com/allen0099/FastAPI-CacheX/coverage-badge/coverage.svg)](https://github.com/allen0099/FastAPI-CacheX/actions/workflows/test.yml)

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
from fastapi_cachex import cache, BackendProxy
from fastapi_cachex.backends import MemoryBackend, MemcachedBackend

app = FastAPI()

# Configure your cache backend
memory_backend = MemoryBackend()  # In-memory cache
# or
memcached_backend = MemcachedBackend(servers=["localhost:11211"])  # Memcached

# Set the backend you want to use
BackendProxy.set_backend(memory_backend)  # or memcached_backend


@app.get("/")
@cache(ttl=60)  # Cache for 60 seconds
async def read_root():
    return {"Hello": "World"}
```

## Backend Configuration

FastAPI-CacheX supports multiple caching backends. You can easily switch between them using the `BackendProxy`.

### In-Memory Cache

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

### Redis (Coming Soon)

Redis support is under development and will be available in future releases.

## Development Guide

### Running Tests

1. Run unit tests:

```bash
pytest
```

2. Run tests with coverage report:

```bash
pytest --cov=fastapi_cachex
```

### Using tox

tox ensures the code works across different Python versions (3.10-3.13).

1. Install all Python versions
2. Run tox:

```bash
tox
```

To run for a specific Python version:

```bash
tox -e py310  # only run for Python 3.10
```

## Contributing

1. Fork the project
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
