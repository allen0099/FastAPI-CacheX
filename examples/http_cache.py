"""HTTP response caching with ``@cache``.

Shows a TTL-cached route with ETag / ``304 Not Modified``, ``no_cache`` and
``private`` routes, clearing a path after an update, and the monitoring routes
behind an admin check.

Run it from a checkout (see ``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/http_cache.py
"""

# --8<-- [start:routes]
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheBackend
from fastapi_cachex import add_routes
from fastapi_cachex import cache
from fastapi_cachex.backends import MemoryBackend

# One backend per process; register it before the first request.
backend = MemoryBackend()
BackendProxy.set(backend)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Stop the memory backend's cleanup task on shutdown."""
    yield
    await backend.aclose()


app = FastAPI(lifespan=lifespan)

PRODUCTS: dict[int, dict[str, object]] = {
    1: {"name": "Keyboard", "price": 49},
    2: {"name": "Mouse", "price": 19},
}

# How often each handler really ran, so a cache hit is visible.
handler_runs: dict[str, int] = {"product": 0, "stock": 0}


@app.get("/products/{product_id}")
@cache(ttl=60)
async def read_product(product_id: int) -> dict[str, object]:
    """Served from the cache for 60 seconds, with an ETag for revalidation."""
    handler_runs["product"] += 1
    if product_id not in PRODUCTS:
        raise HTTPException(status_code=404, detail="Unknown product")
    return {"id": product_id, **PRODUCTS[product_id]}


@app.put("/products/{product_id}")
async def update_product(
    product_id: int, price: int, cache_backend: CacheBackend
) -> dict[str, object]:
    """Change a price, then drop the cached copy so the next GET sees it."""
    if product_id not in PRODUCTS:
        raise HTTPException(status_code=404, detail="Unknown product")
    PRODUCTS[product_id]["price"] = price
    # Memcached cannot match paths; there, use invalidate() (HTTP_CACHING.md).
    await cache_backend.clear_path(f"/products/{product_id}")
    return {"id": product_id, **PRODUCTS[product_id]}


@app.get("/stock/{product_id}")
@cache(ttl=60, no_cache=True)
async def read_stock(product_id: int) -> dict[str, int]:
    """Sent as ``no-cache``: clients revalidate with ``If-None-Match`` each time."""
    handler_runs["stock"] += 1
    return {"id": product_id, "in_stock": 3}


@app.get("/me/preferences")
@cache(ttl=60, private=True)
async def my_preferences() -> dict[str, str]:
    """Sent as ``private``: never stored in the shared backend."""
    # In a real app this depends on the verified caller.
    return {"theme": "dark"}


# --8<-- [end:routes]


# --8<-- [start:admin]
def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Allow the monitoring routes only with the token from ``CACHE_ADMIN_TOKEN``."""
    expected = os.environ.get("CACHE_ADMIN_TOKEN")
    # With no token configured the routes stay closed.
    if not expected or x_admin_token is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


# GET /_cache/cached-hits and /_cache/cached-records. They have no auth of
# their own, so always pass a dependency that has.
add_routes(app, prefix="/_cache", dependencies=[Depends(require_admin)])
# --8<-- [end:admin]
