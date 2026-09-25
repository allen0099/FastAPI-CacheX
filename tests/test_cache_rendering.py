"""@cache builds non-Response results the way FastAPI would (#99)."""

from datetime import datetime
from uuid import UUID

from fastapi import FastAPI
from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

from fastapi_cachex.cache import cache

EVENT_ID = UUID("12345678-1234-5678-1234-567812345678")
EVENT_AT = datetime(2026, 9, 25, 12, 0)  # noqa: DTZ001 - naive encodes the same both ways


class Event(BaseModel):
    id: UUID
    at: datetime


class UserIn(BaseModel):
    name: str
    password: str


class UserOut(BaseModel):
    name: str
    nickname: str | None = None


def test_pydantic_result_with_non_json_types_is_encoded() -> None:
    app = FastAPI()

    @app.get("/event")
    @cache(ttl=60)
    async def event() -> Event:
        return Event(id=EVENT_ID, at=EVENT_AT)

    @app.get("/raw")
    @cache(ttl=60)
    async def raw():  # unannotated, so there is no response model
        return {"id": EVENT_ID, "at": EVENT_AT}

    expected = {"id": str(EVENT_ID), "at": "2026-09-25T12:00:00"}
    with TestClient(app) as client:
        for path in ("/event", "/raw"):
            first = client.get(path)
            assert first.status_code == 200
            assert first.json() == expected
            # The second request is a cache hit and must match the miss.
            assert client.get(path).json() == expected


def test_response_model_filters_fields() -> None:
    app = FastAPI()

    @app.get("/user", response_model=UserOut, response_model_exclude_none=True)
    @cache(ttl=60)
    async def user() -> UserIn:
        return UserIn(name="alice", password="hunter2")

    with TestClient(app) as client:
        response = client.get("/user")

    assert response.status_code == 200
    assert response.json() == {"name": "alice"}


def test_route_status_code_is_used() -> None:
    app = FastAPI()

    @app.get("/accepted", status_code=202)
    @cache(ttl=60)
    async def accepted() -> dict[str, str]:
        return {"queued": "yes"}

    with TestClient(app) as client:
        miss = client.get("/accepted")
        hit = client.get("/accepted")

    assert miss.status_code == 202
    assert hit.status_code == 202
    assert hit.json() == {"queued": "yes"}


def test_no_content_status_drops_the_body() -> None:
    app = FastAPI()

    @app.get("/empty", status_code=204)
    @cache(ttl=60)
    async def empty() -> None:
        return None

    with TestClient(app) as client:
        response = client.get("/empty")

    assert response.status_code == 204
    assert response.content == b""


def test_injected_response_headers_and_status_are_kept() -> None:
    app = FastAPI()

    @app.get("/tagged")
    @cache(ttl=60)
    async def tagged(response: Response) -> dict[str, str]:
        response.headers["X-Tag"] = "blue"
        response.status_code = 203
        return {"tag": "blue"}

    with TestClient(app) as client:
        miss = client.get("/tagged")
        hit = client.get("/tagged")

    for response in (miss, hit):
        assert response.status_code == 203
        assert response.headers["x-tag"] == "blue"
        assert response.json() == {"tag": "blue"}
