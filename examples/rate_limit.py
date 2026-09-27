"""A fixed-window rate limiter on ``backend.increment``.

Each client may call ``/search`` ``LIMIT`` times per ``WINDOW`` seconds; the
next call gets ``429 Too Many Requests`` with a ``Retry-After`` header. The
counter is one atomic ``increment`` per request, so concurrent requests (and,
with Redis, several workers) never lose a count.

Run it from a checkout (see ``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/rate_limit.py
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheBackend
from fastapi_cachex.backends import MemoryBackend

backend = MemoryBackend()
BackendProxy.set(backend)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Stop the memory backend's cleanup task on shutdown."""
    yield
    await backend.aclose()


app = FastAPI(lifespan=lifespan)

LIMIT = 5
WINDOW = 60  # seconds


async def rate_limit(request: Request, cache_backend: CacheBackend) -> None:
    """Allow ``LIMIT`` requests per client per window, then answer ``429``."""
    # Behind a reverse proxy, request.client is the proxy: derive the client
    # address from a header only your proxy can set.
    client = request.client.host if request.client else "unknown"
    window = int(time.time()) // WINDOW
    # A new key per window; the TTL only applies when the counter is created,
    # so it expires with its window and never needs deleting.
    count = await cache_backend.increment(f"ratelimit:{client}:{window}", ttl=WINDOW)
    if count > LIMIT:
        retry_after = WINDOW - int(time.time()) % WINDOW
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(retry_after)},
        )


@app.get("/search", dependencies=[Depends(rate_limit)])
async def search(q: str) -> dict[str, list[str]]:
    """A route worth protecting."""
    return {"results": [f"{q} result"]}
