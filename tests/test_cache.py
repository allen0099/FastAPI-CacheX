from fastapi import FastAPI
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from fastapi_cachex.cache import cache

app = FastAPI()
client = TestClient(app)


def test_default_cache():
    @app.get("/default")
    @cache()
    async def default_cache_endpoint():
        return Response(
            content=b'{"message": "This is a default cache endpoint"}',
            media_type="application/json",
        )

    response = client.get("/default")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == ""
    assert "ETag" in response.headers


def test_ttl_endpoint():
    @app.get("/ttl")
    @cache(60)
    async def ttl_endpoint():
        return Response(
            content=b'{"message": "This endpoint has a TTL of 60 seconds"}',
            media_type="application/json",
        )

    response = client.get("/ttl")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "max-age=60"


def test_no_cache_endpoint():
    @app.get("/no-cache")
    @cache(no_cache=True)
    async def no_cache_endpoint():
        return Response(
            content=b'{"message": "This endpoint should not be cached"}',
            media_type="application/json",
        )

    response = client.get("/no-cache")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"


def test_no_store_endpoint():
    @app.get("/no-store")
    @cache(no_store=True)
    async def no_store_endpoint():
        return Response(
            content=b'{"message": "This endpoint must not be stored"}',
            media_type="application/json",
        )

    response = client.get("/no-store")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_public_private_endpoints():
    @app.get("/public")
    @cache(public=True)
    async def public_endpoint():
        return Response(
            content=b'{"message": "This is a public endpoint"}',
            media_type="application/json",
        )

    @app.get("/private")
    @cache(private=True)
    async def private_endpoint():
        return Response(
            content=b'{"message": "This is a private endpoint"}',
            media_type="application/json",
        )

    public_response = client.get("/public")
    assert public_response.status_code == 200
    assert "public" in public_response.headers["Cache-Control"].lower()

    private_response = client.get("/private")
    assert private_response.status_code == 200
    assert "private" in private_response.headers["Cache-Control"].lower()


def test_etag_handling():
    @app.get("/etag")
    @cache()
    async def etag_endpoint():
        return Response(
            content=b'{"message": "This endpoint supports ETag"}',
            media_type="application/json",
        )

    # First request - should get the full response
    response1 = client.get("/etag")
    assert response1.status_code == 200
    assert "ETag" in response1.headers

    # Second request with If-None-Match header
    etag = response1.headers["ETag"]
    response2 = client.get("/etag", headers={"If-None-Match": etag})
    assert response2.status_code == 304  # Not Modified


def test_stale_responses():
    @app.get("/stale-while-revalidate")
    @cache(stale="revalidate", stale_ttl=30)
    async def stale_while_revalidate_endpoint():
        return Response(
            content=b'{"message": "This endpoint allows stale content while revalidating"}',
            media_type="application/json",
        )

    @app.get("/stale-if-error")
    @cache(stale="error", stale_ttl=60)
    async def stale_if_error_endpoint():
        return Response(
            content=b'{"message": "This endpoint allows stale content on error"}',
            media_type="application/json",
        )

    response1 = client.get("/stale-while-revalidate")
    assert response1.status_code == 200
    assert "stale-while-revalidate=30" in response1.headers["Cache-Control"]

    response2 = client.get("/stale-if-error")
    assert response2.status_code == 200
    assert "stale-if-error=60" in response2.headers["Cache-Control"]


def test_broken_stale():
    @app.get("/stale")
    @cache(stale="revalidate")
    async def stale_broken_endpoint():
        return Response(
            content=b'{"message": "This endpoint allows stale content"}',
            media_type="application/json",
        )

    try:
        client.get("/stale")

    except Exception as e:
        assert "CacheXError" in str(type(e).__name__)
        assert "stale_ttl must be set if stale is used" in str(e)


def test_positional_args():
    @app.get("/positional-args/{arg}")
    @cache()
    async def positional_args_endpoint(arg: str, *, name: str = "default"):
        return Response(
            content=b'{"message": "This endpoint uses positional args"}',
            media_type="application/json",
        )

    response = client.get("/positional-args/test")
    assert response.status_code == 200


def test_sync_endpoint():
    @app.get("/sync")
    @cache()
    def sync_endpoint():
        return Response(
            content=b'{"message": "This is a synchronous endpoint"}',
            media_type="application/json",
        )

    response = client.get("/sync")
    assert response.status_code == 200


def test_no_cache_with_revalidate():
    @app.get("/no-cache-revalidate")
    @cache(no_cache=True, must_revalidate=True)
    async def no_cache_revalidate_endpoint():
        return Response(
            content=b'{"message": "This endpoint should not be cached but must revalidate"}',
            media_type="application/json",
        )

    response = client.get("/no-cache-revalidate")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache, must-revalidate"


def test_must_revalidate_endpoint():
    @app.get("/must-revalidate")
    @cache(must_revalidate=True)
    async def must_revalidate_endpoint():
        return Response(
            content=b'{"message": "This endpoint must revalidate"}',
            media_type="application/json",
        )

    response = client.get("/must-revalidate")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "must-revalidate"


def test_immutable_endpoint():
    @app.get("/immutable")
    @cache(immutable=True)
    async def immutable_endpoint():
        return Response(
            content=b'{"message": "This endpoint is immutable"}',
            media_type="application/json",
        )

    response = client.get("/immutable")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "immutable"


def test_json_response():
    @app.get("/json-response")
    @cache()
    async def json_response_endpoint():
        return JSONResponse(
            content={"message": "This is a JSON response"},
            media_type="application/json",
        )

    response = client.get("/json-response")
    assert response.status_code == 200


def test_param_var_keyword():
    @app.get("/param-keyword")
    @cache()
    async def param_keyword_endpoint(param: str = "default"):
        return Response(
            content=b'{"message": "This endpoint uses param and keyword"}',
            media_type="application/json",
        )

    response = client.get("/param-keyword?param=test&keyword=value")
    assert response.status_code == 200


def test_contain_request():
    @app.get("/contain-request")
    @cache()
    async def contain_request_endpoint(request: Request):
        return Response(
            content=b'{"message": "This endpoint contains request"}',
            media_type="application/json",
        )

    response = client.get("/contain-request")
    assert response.status_code == 200
    assert "ETag" in response.headers


def test_post_should_not_cache():
    @app.post("/post")
    @cache()
    async def post_endpoint():
        return Response(
            content=b'{"message": "This is a POST endpoint"}',
            media_type="application/json",
        )

    response = client.post("/post")
    assert response.status_code == 200
    assert "cache-control" not in response.headers
