import asyncio

import pytest

from fastapi_cachex.backends.memory import MemoryBackend

# from fastapi_cachex.proxy import BackendProxy


@pytest.fixture(autouse=True, scope="session")
async def init_cache():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    backend = MemoryBackend()
    # BackendProxy.set_backend(backend)
    task = loop.create_task(backend._cleanup_task())
    yield
    await task
    loop.close()
    # Teardown code here if needed
