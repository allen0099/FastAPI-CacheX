"""Caching your own values with ``CacheManager``.

``get_or_set`` computes a value once and serves it from the cache until it
expires; ``add`` stores a key only if it is absent, which makes a simple
idempotency check. Both come through the ``AppCache`` dependency.

Run it from a checkout (see ``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/app_cache.py
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException

from fastapi_cachex import AppCache
from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheManager
from fastapi_cachex import CacheManagerProxy
from fastapi_cachex.backends import MemoryBackend

backend = MemoryBackend()
BackendProxy.set(backend)
# Optional: without this, AppCache creates a CacheManager with the defaults
# (key prefix "cache:", no TTL) on first use.
CacheManagerProxy.set(CacheManager(key_prefix="example:", default_ttl=300))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Stop the memory backend's cleanup task on shutdown."""
    yield
    await backend.aclose()


app = FastAPI(lifespan=lifespan)

# How often the "slow upstream" was really called.
upstream_calls: dict[str, int] = {"rates": 0}


async def fetch_rates() -> dict[str, float]:
    """Stand-in for a slow call to another service."""
    upstream_calls["rates"] += 1
    return {"EUR": 0.92, "JPY": 151.3}


@app.get("/rates")
async def read_rates(app_cache: AppCache) -> dict[str, float]:
    """Call the upstream once, then answer from the cache for 60 seconds."""
    rates: dict[str, float] = await app_cache.get_or_set("rates", fetch_rates, ttl=60)
    return rates


@app.post("/orders", status_code=201)
async def create_order(
    app_cache: AppCache, idempotency_key: Annotated[str, Header()]
) -> dict[str, str]:
    """Accept each ``Idempotency-Key`` once; a retry gets ``409``."""
    order_id = secrets.token_hex(8)
    # add() is atomic: of two concurrent retries exactly one gets True.
    if not await app_cache.add(f"order:{idempotency_key}", order_id, ttl=3600):
        existing = await app_cache.get(f"order:{idempotency_key}")
        raise HTTPException(
            status_code=409, detail=f"Already accepted as order {existing}"
        )
    return {"order_id": order_id}


@app.delete("/rates")
async def forget_rates(app_cache: AppCache) -> dict[str, bool]:
    """Drop the cached rates; the next read calls the upstream again."""
    return {"deleted": await app_cache.delete("rates")}
