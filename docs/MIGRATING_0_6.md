# Migrating to 0.6.0 {#migrating-to-060}

0.6.0 is not released yet. Its breaking changes are collected in the [0.6.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.6.0), and this page grows as they land.

## Python 3.11 or later {#python-311}

Python 3.10 reached its end of life in October 2026, and 0.6.0 requires Python 3.11 or later ([#491](https://github.com/allen0099/FastAPI-CacheX/issues/491)). The 0.5.x releases from 0.5.2 on say so first: importing `fastapi_cachex` under Python 3.10 emits a `FutureWarning` once per process:

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

The `redis` extra now needs `orjson` 3.8.0 or later (it was 3.4.7): 3.8.0 is the first release with Python 3.11 wheels on Linux, macOS and Windows. An environment that pins an older `orjson` has to raise the pin.

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

A test suite that turns warnings into errors (`filterwarnings = ["error"]`) fails on 0.5.3 and later 0.5.x releases wherever it builds the backend without both arguments. If the backend is built by code you cannot change, ignore this one warning instead:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore:MemcachedBackend timeouts will default to 1 second:FutureWarning",
]
```

Outside pytest, `warnings.filterwarnings("ignore", message="MemcachedBackend timeouts will default to 1 second", category=FutureWarning)` before the backend is built does the same, as does `PYTHONWARNINGS="ignore:MemcachedBackend timeouts will default to 1 second:FutureWarning"` in the environment.

With `@cache`, a timeout is a backend failure like any other: the request is [answered without the cache](HTTP_CACHING.md#when-the-backend-fails). A server that regularly takes longer than a second to answer needs the larger value spelled out.

## `@cached` validates into the return annotation {#cached-return-annotation}

Until 0.6.0, a function decorated with `@cached` returns the JSON form of its value unless `type_=` is passed: a function annotated `-> User` gets a `dict` back, and one annotated `-> tuple[int, int]` a list, on the first call and on every hit. From 0.6.0, when `type_` is not passed, `@cached` validates the cached value into the function's return annotation ([#533](https://github.com/allen0099/FastAPI-CacheX/issues/533)).

From 0.5.6 on, applying `@cached` without `type_` to a function this will affect emits a `FutureWarning`:

```text
FutureWarning: cached return annotation: load_user() is annotated to return <class 'app.User'>, and from 0.6.0 @cached validates the cached value into that type ...
```

It is emitted once, where the decorator is applied, and only when the change can alter what the function returns. It stays quiet when:

- `type_` is passed, whatever its value;
- the function has no return annotation;
- the annotation is built only from JSON's own types: `str`, `int`, `float`, `bool`, `None`, `Any`, `object`, `list` and `dict` (bare, or as `list[...]` and `dict[str, ...]`), and unions of them. For these the value is returned as JSON decoded it and is never converted.

A return annotation that cannot be resolved where the decorator is applied gets a warning of its own, with the same prefix. That happens when the type is imported under `if TYPE_CHECKING:` (linters often move imports there) or is a class defined further down the file. 0.6.0 cannot read such an annotation, so that function would keep returning the JSON form while the others change; passing `type_` settles it either way.

Choose per function, and the warning goes away:

```python
@cached(ttl=60, type_=User)  # the 0.6.0 behaviour, now
async def load_user(user_id: int) -> User: ...


@cached(ttl=60, type_=None)  # keep the JSON form, in 0.6.0 too
async def load_user_dict(user_id: int) -> User: ...
```

With `type_=User`, a value that was cached as something that no longer fits the type counts as a miss and the function runs again, so a deploy needs no cache flush.

To silence the warning for a whole test suite instead:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore:cached return annotation:FutureWarning",
]
```
