import pytest_asyncio

from fastapi_cachex.backends.memory import MemoryBackend


@pytest_asyncio.fixture(autouse=True)
async def init_cache():
    backend = MemoryBackend()
    backend.start_cleanup()  # 開始清理任務
    yield backend
    backend.stop_cleanup()  # 停止清理任務
