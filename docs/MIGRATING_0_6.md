# Migrating to 0.6.0 {#migrating-to-060}

0.6.0 is not released yet. Its breaking changes are collected in the [0.6.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.6.0), and this page grows as they land.

## Python 3.11 or later {#python-311}

Python 3.10 reached its end of life in October 2026, and 0.6.0 requires Python 3.11 or later ([#491](https://github.com/allen0099/FastAPI-CacheX/issues/491)). From the next 0.5.x release on, importing `fastapi_cachex` under Python 3.10 emits a `FutureWarning` once per process:

```text
FutureWarning: fastapi-cachex 0.6.0 will require Python 3.11 or later; ...
```

On Python 3.10 nothing breaks by itself. `pip` and `uv` read `requires-python` and keep installing the newest 0.5.x. But releases come only from `master`, so 0.5.x releases, security fixes included, stop as soon as the first 0.6.0 change is merged, which can be well before 0.6.0 itself ships (see the [security policy](https://github.com/allen0099/FastAPI-CacheX/blob/master/SECURITY.md)). So the fix is to move to Python 3.11 or later.

The warning comes from the import, not from any call your code makes, so no change to your code removes it. A test suite that turns warnings into errors (`filterwarnings = ["error"]`) fails on Python 3.10 until you either upgrade Python or ignore this one warning:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore:fastapi-cachex 0.6.0 will require Python 3.11:FutureWarning",
]
```

To silence it outside pytest, install the filter before the first `import fastapi_cachex`:

```python
import warnings

warnings.filterwarnings(
    "ignore",
    message="fastapi-cachex 0.6.0 will require Python 3.11",
    category=FutureWarning,
)

import fastapi_cachex  # noqa: E402
```

or set `PYTHONWARNINGS="ignore:fastapi-cachex 0.6.0 will require Python 3.11:FutureWarning"` in the environment.

## Memcached timeouts default to 1 second {#memcached-timeouts}

`MemcachedBackend` waits 5 seconds for a connection and for each reply unless told otherwise. In 0.6.0 both defaults become 1 second, as on the Redis backend ([#516](https://github.com/allen0099/FastAPI-CacheX/issues/516)): every Memcached call holds a worker thread until the server answers or the timeout passes, so a long default lets an unreachable server tie up the thread pool your sync handlers and dependencies share.

From 0.5.3 on, building the backend without both arguments emits a `FutureWarning`:

```text
FutureWarning: MemcachedBackend timeouts will default to 1 second in fastapi-cachex 0.6.0; ...
```

Pass both to choose the values now; that also silences the warning:

```python
backend = MemcachedBackend(
    servers=["localhost:11211"],
    connect_timeout=1.0,  # or 5.0 to keep today's behaviour
    timeout=1.0,
)
```

With `@cache`, a timeout is a backend failure like any other: the request is [answered without the cache](HTTP_CACHING.md#when-the-backend-fails). A server that regularly takes longer than a second to answer needs the larger value spelled out.
