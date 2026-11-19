"""What a type checker sees for typed reads and cached methods (#580).

``mypy tests`` checks the ``assert_type`` calls; at runtime they do nothing,
so these tests also run the calls they type.
"""

from typing import Any
from typing import assert_type

from pydantic import BaseModel

from fastapi_cachex import CacheManager
from fastapi_cachex import cached
from fastapi_cachex.backends.memory import MemoryBackend


class _User(BaseModel):
    name: str


async def test_typed_reads_return_the_type() -> None:
    manager = CacheManager(backend=MemoryBackend())

    user = await manager.get_or_set("u", lambda: {"name": "a"}, type_=_User)
    assert_type(user, _User)
    users = await manager.get_many(["u"], type_=_User)
    assert_type(users, dict[str, _User])
    assert_type(await manager.get_or_set("v", lambda: 1), Any)
    assert_type(await manager.get_many(["v"]), dict[str, Any])
    assert user == _User(name="a")
    assert users == {"u": user}


class _Repo:
    @cached(ttl=60, key=lambda self, user_id: f"user:{user_id}")
    async def load(self, user_id: int) -> int:
        return user_id


async def test_a_cached_method_keeps_its_parameters() -> None:
    repo = _Repo()
    assert_type(await repo.load(1), int)
    assert repo.load.cache_key(1) == "user:1"
    # Wrong arguments are flagged on a method as on a function.
    await repo.load("1")  # type: ignore[arg-type]
    repo.load.cache_key("1")  # type: ignore[arg-type]
    await repo.load.invalidate("1")  # type: ignore[arg-type]
