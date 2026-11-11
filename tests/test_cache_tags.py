"""``@cache(tags=...)``, ``@cached(tags=...)`` and ``invalidate_tags()`` (#538)."""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.testclient import TestClient

from fastapi_cachex import CacheManager
from fastapi_cachex import _stored_response as stored_response
from fastapi_cachex import cache
from fastapi_cachex import cached
from fastapi_cachex import invalidate_tags
from fastapi_cachex._tags import validate_tags
from fastapi_cachex.backends.codec import decode_entry
from fastapi_cachex.backends.codec import encode_entry
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.exceptions import CacheXError
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import CacheEntry


def _app(**cache_kwargs: Any) -> tuple[TestClient, list[str]]:
    """Routes that answer with how often their handler ran."""
    app = FastAPI()
    runs: list[str] = []
    cache_kwargs.setdefault("debug_header", True)

    @app.get("/users/{user_id}")
    @cache(ttl=60, **cache_kwargs)
    async def user(user_id: int) -> dict[str, int]:
        runs.append(f"user {user_id}")
        return {"runs": len(runs)}

    @app.get("/plain")
    @cache(ttl=60, debug_header=True)
    async def plain() -> dict[str, int]:
        runs.append("plain")
        return {"runs": len(runs)}

    @app.post("/invalidate/{tag}")
    async def drop(tag: str) -> None:
        await invalidate_tags(tag)

    return TestClient(app), runs


def _x_cache(client: TestClient, path: str) -> str:
    return str(client.get(path).headers["x-cache"])


def test_invalidating_a_tag_makes_its_entries_a_miss() -> None:
    client, runs = _app(tags=["users"])

    assert _x_cache(client, "/users/1") == "MISS"
    assert _x_cache(client, "/users/1") == "HIT"
    assert _x_cache(client, "/plain") == "MISS"

    client.post("/invalidate/users")

    assert _x_cache(client, "/users/1") == "MISS"
    assert _x_cache(client, "/users/1") == "HIT"
    # A route without the tag is left alone.
    assert _x_cache(client, "/plain") == "HIT"
    assert runs == ["user 1", "plain", "user 1"]


def test_tags_from_the_request_invalidate_one_users_entries() -> None:
    client, _ = _app(
        tags=lambda request: ["users", f"user:{request.path_params['user_id']}"]
    )
    client.get("/users/1")
    client.get("/users/2")

    client.post("/invalidate/user:1")

    assert _x_cache(client, "/users/1") == "MISS"
    assert _x_cache(client, "/users/2") == "HIT"
    # The tag both share invalidates both.
    client.post("/invalidate/users")
    assert _x_cache(client, "/users/1") == "MISS"
    assert _x_cache(client, "/users/2") == "MISS"


def test_an_untagged_route_does_no_extra_backend_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _app()
    batched: list[Any] = []
    get_many = MemoryBackend.get_many

    async def counting(self: MemoryBackend, keys: Any) -> Any:
        batched.append(keys)
        return await get_many(self, keys)

    monkeypatch.setattr(MemoryBackend, "get_many", counting)

    client.get("/users/1")
    client.get("/users/1")

    assert batched == []


def test_a_response_rendered_before_an_invalidation_is_not_served_after_it() -> None:
    """The tag versions are read before the handler runs."""
    app = FastAPI()
    runs: list[int] = []

    @app.get("/report")
    @cache(ttl=60, tags=["report"], debug_header=True)
    async def report() -> dict[str, int]:
        runs.append(1)
        if len(runs) == 1:
            # The data changes while this response is being built from it.
            await invalidate_tags("report")
        return {"runs": len(runs)}

    client = TestClient(app)

    assert client.get("/report").json() == {"runs": 1}
    second = client.get("/report")
    assert (second.json(), second.headers["x-cache"]) == ({"runs": 2}, "MISS")
    assert client.get("/report").headers["x-cache"] == "HIT"


async def test_the_app_cache_and_the_routes_share_the_tags() -> None:
    client, _ = _app(tags=["users"])
    manager = CacheManager(BackendProxy.get())
    client.get("/users/1")
    await manager.set("user-count", 1, ttl=60, tags=["users"])

    await manager.invalidate_tags("users")

    assert _x_cache(client, "/users/1") == "MISS"
    assert await manager.get("user-count") is None


def test_a_stale_entry_whose_tag_was_invalidated_is_not_served_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [stored_response._now()]
    monkeypatch.setattr(stored_response, "_now", lambda: now[0])
    client, _ = _app(tags=["users"], stale="revalidate", stale_ttl=30, serve_stale=True)
    client.get("/users/1")
    now[0] += 61
    client.post("/invalidate/users")

    response = client.get("/users/1")

    assert (response.json(), response.headers["x-cache"]) == ({"runs": 2}, "MISS")


async def test_cached_functions_take_tags_as_a_list_or_from_the_arguments() -> None:
    manager = CacheManager(BackendProxy.get())
    calls: list[str] = []

    @cached(ttl=60, manager=manager, tags=["users"])
    async def count_users() -> int:
        calls.append("count")
        return len(calls)

    @cached(ttl=60, manager=manager, tags=lambda user_id: [f"user:{user_id}"])
    async def load_user(user_id: int) -> int:
        calls.append(f"user {user_id}")
        return len(calls)

    await count_users()
    await load_user(1)
    await load_user(2)

    await invalidate_tags("user:1")
    await count_users()
    await load_user(1)
    await load_user(2)

    assert calls == ["count", "user 1", "user 2", "user 1"]


async def test_a_value_without_tags_creates_no_tag_state() -> None:
    manager = CacheManager(BackendProxy.get())

    await manager.set("plain", 1, ttl=60)
    await manager.invalidate_tags()

    assert await BackendProxy.get().get_all_keys() == ["cache:plain"]


@pytest.mark.parametrize(
    ("tags", "message"),
    [
        ("users", "not a single str"),
        ([1], "a tag must be a str"),
        ([""], "a tag cannot be empty"),
        (5, "iterable of tag names"),
    ],
)
def test_tags_that_are_not_a_list_of_names_are_rejected(
    tags: Any, message: str
) -> None:
    with pytest.raises(CacheXError, match=message):
        _app(tags=tags)


def test_an_async_tags_callable_is_rejected() -> None:
    async def tags(request: Request) -> list[str]:
        return ["users"]

    with pytest.raises(CacheXError, match="plain function, not async"):
        _app(tags=tags)


async def test_invalidate_tags_checks_the_names_and_needs_no_backend() -> None:
    with pytest.raises(TypeError, match="a tag must be a str"):
        await invalidate_tags(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="a tag cannot be empty"):
        await invalidate_tags("")
    BackendProxy.set(None)

    await invalidate_tags("users")


def test_no_store_route_warns_that_tags_are_ignored() -> None:
    with pytest.warns(UserWarning, match="tags"):
        _app(no_store=True, tags=["users"])


def test_the_stored_document_has_tags_only_when_the_entry_does() -> None:
    plain = CacheEntry(fingerprint="etag", content=b"x")
    tagged = CacheEntry(fingerprint="etag", content=b"x", tags=(("users", "v1"),))

    assert b"tags" not in encode_entry(plain)
    assert decode_entry(encode_entry(plain)) == plain
    assert decode_entry(encode_entry(tagged)) == tagged


@pytest.mark.parametrize("tags", ['["users"]', '{"users": 1}', '"users"'])
def test_a_document_whose_tags_cannot_be_read_is_a_miss(tags: str) -> None:
    """Never served as if it had none."""
    raw = encode_entry(CacheEntry(fingerprint="etag", content=b"x"))[:-1]

    assert decode_entry(raw + b', "tags": ' + tags.encode() + b"}") is None


def test_a_request_that_stores_nothing_creates_no_tag() -> None:
    """Otherwise every unknown id in a URL would leave a key behind."""
    app = FastAPI()

    @app.get("/items/{name}")
    @cache(ttl=60, tags=lambda request: [f"item:{request.path_params['name']}"])
    async def item(name: str) -> None:
        raise HTTPException(status_code=404)

    client = TestClient(app)
    for name in ("a", "b", "c"):
        assert client.get(f"/items/{name}").status_code == 404

    assert BackendProxy.get().cache == {}  # type: ignore[attr-defined]


def test_a_stale_entry_refreshed_in_the_background_keeps_its_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [stored_response._now()]
    monkeypatch.setattr(stored_response, "_now", lambda: now[0])
    client, _ = _app(tags=["users"], stale="revalidate", stale_ttl=30, serve_stale=True)
    client.get("/users/1")
    now[0] += 61
    assert _x_cache(client, "/users/1") == "STALE"  # and refreshed after it
    assert _x_cache(client, "/users/1") == "HIT"

    client.post("/invalidate/users")

    assert _x_cache(client, "/users/1") == "MISS"


class _TagsUnreadable(MemoryBackend):
    """The entry can be read; the tag versions cannot."""

    async def get_many(self, keys: Any) -> dict[str, CacheEntry]:
        msg = "backend is down"
        raise ConnectionError(msg)


async def test_a_failed_tag_read_serves_the_response_without_storing_it() -> None:
    backend = _TagsUnreadable()
    BackendProxy.set(backend)
    client, runs = _app(tags=["users"])
    try:
        assert client.get("/users/1").status_code == 200
        assert client.get("/users/1").status_code == 200
    finally:
        await backend.aclose()

    assert runs == ["user 1", "user 1"]
    assert backend.cache == {}


async def test_a_failed_tag_read_fails_the_request_when_failing_closed() -> None:
    backend = _TagsUnreadable()
    BackendProxy.set(backend)
    client = TestClient(
        _app(tags=["users"], fail_open=False)[0].app, raise_server_exceptions=False
    )
    try:
        assert client.get("/users/1").status_code == 500
    finally:
        await backend.aclose()


def test_a_tags_function_returning_a_bad_value_is_an_error_of_the_route() -> None:
    client = TestClient(
        _app(tags=lambda request: [request.headers.get("x-tenant", "")])[0].app,
        raise_server_exceptions=False,
    )

    assert client.get("/users/1", headers={"X-Tenant": "acme"}).status_code == 200
    assert client.get("/users/2").status_code == 500


def test_tag_names_are_distinct_and_in_a_stable_order() -> None:
    assert validate_tags(["b", "a", "b"]) == ("a", "b")
    assert validate_tags(name for name in ("z", "y")) == ("y", "z")
    assert validate_tags(None) == ()


def _users_app(**cache_kwargs: Any) -> TestClient:
    """One deploy of a route; the deploys share the backend."""
    app = FastAPI()

    @app.get("/users/{user_id}")
    @cache(ttl=60, debug_header=True, **cache_kwargs)
    async def user(user_id: int) -> dict[str, int]:
        return {"id": user_id}

    return TestClient(app)


@pytest.mark.parametrize("before", [{}, {"tags": ["people"]}], ids=["none", "renamed"])
def test_an_entry_stored_before_the_route_had_its_tags_is_not_served(
    before: dict[str, Any],
) -> None:
    """It could not be invalidated by them: it is rendered again, tagged."""
    _users_app(**before).get("/users/1")
    deployed = _users_app(tags=["users"])

    assert _x_cache(deployed, "/users/1") == "MISS"
    assert _x_cache(deployed, "/users/1") == "HIT"


def test_a_tags_function_runs_once_per_request() -> None:
    calls: list[str] = []

    def tags(request: Request) -> list[str]:
        calls.append(request.url.path)
        return ["users"]

    client = _users_app(tags=tags)

    client.get("/users/1")  # a miss: read, then stored
    assert calls == ["/users/1"]
    client.get("/users/1")  # a hit
    assert calls == ["/users/1", "/users/1"]
