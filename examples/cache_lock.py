"""Mutual exclusion across workers with ``CacheLock``.

``/reports/{name}/rebuild`` waits for the lock, so concurrent rebuilds of the
same report run one after the other. ``/imports`` does not wait: a second
import while one is running gets ``409``. With a shared backend such as Redis
this holds across processes and machines, not just within one.

Run it from a checkout (see ``examples/README.md``)::

    uv run --with "fastapi-cli[standard]" fastapi dev examples/cache_lock.py
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import HTTPException

from fastapi_cachex import BackendProxy
from fastapi_cachex import CacheLock
from fastapi_cachex import LockTimeoutError
from fastapi_cachex.backends import MemoryBackend

backend = MemoryBackend()
BackendProxy.set(backend)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Stop the memory backend's cleanup task on shutdown."""
    yield
    await backend.aclose()


app = FastAPI(lifespan=lifespan)

# How many rebuilds run right now, and the most that ever ran at once.
rebuilds = {"running": 0, "max_running": 0}


async def rebuild_report() -> None:
    """Stand-in for slow work that must not run twice at the same time."""
    rebuilds["running"] += 1
    rebuilds["max_running"] = max(rebuilds["max_running"], rebuilds["running"])
    try:
        await asyncio.sleep(0.2)
    finally:
        rebuilds["running"] -= 1


@app.post("/reports/{name}/rebuild")
async def rebuild(name: str) -> dict[str, str]:
    """Wait up to 10 seconds for the lock, then rebuild."""
    try:
        # ttl bounds how long a crashed holder keeps the lock; make it longer
        # than the work, or call lock.extend() while working.
        async with CacheLock(f"report:{name}", ttl=30, timeout=10):
            await rebuild_report()
    except LockTimeoutError as exc:
        raise HTTPException(status_code=503, detail="Report is busy") from exc
    return {"rebuilt": name}


@app.post("/imports")
async def start_import() -> dict[str, str]:
    """Refuse at once when another import holds the lock."""
    # One CacheLock instance per acquisition.
    lock = CacheLock("import", ttl=60)
    if not await lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An import is already running")
    try:
        await asyncio.sleep(0.2)  # the import itself
    finally:
        await lock.release()
    return {"imported": "ok"}
