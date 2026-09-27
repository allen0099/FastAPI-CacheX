"""Using Redis as the backend.

The backend is built from environment variables when the app starts and its
connection pool is closed on shutdown. Every other example works the same
way on Redis: replace its ``MemoryBackend`` with this setup.

Needs the ``redis`` extra (``uv add "fastapi-cachex[redis]"``) and a Redis
server. The settings are read from ``REDIS_HOST`` (default ``127.0.0.1``),
``REDIS_PORT`` (``6379``), ``REDIS_DB`` (``0``) and ``REDIS_PASSWORD``
(unset). Run it from a checkout (see ``examples/README.md``)::

    REDIS_PORT=6379 uv run --with "fastapi-cli[standard]" fastapi dev examples/redis_backend.py
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheBackend
from fastapi_cachex import cache
from fastapi_cachex.backends import AsyncRedisCacheBackend


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Connect to Redis on startup and close the connection pool on shutdown."""
    backend = AsyncRedisCacheBackend(
        host=os.environ.get("REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        db=int(os.environ.get("REDIS_DB", "0")),
        password=os.environ.get("REDIS_PASSWORD") or None,
        # Namespaces this app's keys on a shared server.
        key_prefix="fastapi_cachex_example:",
    )
    BackendProxy.set(backend)
    try:
        yield
    finally:
        BackendProxy.set(None)
        # The types-redis stubs predate aclose() (redis-py 5.0.1+).
        await backend.client.aclose()  # type: ignore[attr-defined]


app = FastAPI(lifespan=lifespan)


@app.get("/hello/{name}")
@cache(ttl=60)
async def hello(name: str) -> dict[str, str]:
    """Cached in Redis, so every worker process shares the entry."""
    return {"hello": name}


@app.post("/visits")
async def count_visit(cache_backend: CacheBackend) -> dict[str, int]:
    """A counter shared by every worker, incremented atomically on Redis."""
    visits = await cache_backend.increment("visits")
    return {"visits": visits}
