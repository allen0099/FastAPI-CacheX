# Examples

Each file here is a complete FastAPI app that shows one feature of
FastAPI-CacheX. They use only the public API and the in-memory backend (except
`redis_backend.py` and `session_redis.py`), so they run without any server.

The `session_*.py` and `oauth_state.py` examples use `fastapi_cachex.session`
and `fastapi_cachex.state`, which are deprecated in 0.4.0 and removed in 0.5.0
([where to move](https://fastapi-cachex.readthedocs.io/en/latest/MIGRATING_0_4/#session-state-deprecated)).

| Example | What it shows | Needs |
|---------|---------------|-------|
| [`http_cache.py`](http_cache.py) | `@cache` with a TTL, ETag and `304 Not Modified`, `no_cache` and `private` routes, `clear_path()` after an update, the monitoring routes behind an admin check | — |
| [`app_cache.py`](app_cache.py) | `CacheManager.get_or_set()` for an expensive call, `add()` as an idempotency check, the `AppCache` dependency | — |
| [`session_login.py`](session_login.py) | `FastAPICacheXSessionMiddleware` with cookies: an anonymous session, login with `login()`, `AuthenticatedSession`, logout with `request.session.clear()` | — |
| [`session_api.py`](session_api.py) | Sessions for an API client that keeps its own token: returned by `/login`, sent back as `Authorization: Bearer` or `X-Session-Token`; `AuthenticatedSession`, `OptionalSession`, logout with `delete_session()` | — |
| [`session_redis.py`](session_redis.py) | Sessions on Redis with IP binding and sliding expiration, flash messages, `update_session()` and logout on every device with `delete_user_sessions()` | `redis` extra, a Redis server |
| [`session_jwt.py`](session_jwt.py) | Sessions with `token_format="jwt"` for API clients (`Authorization: Bearer`), revoked on logout | `jwt` extra |
| [`session_jwt_claims.py`](session_jwt_claims.py) | A custom JWT serializer passed as `token_serializer`, adding `tenant_id` and `api_version` claims and rejecting other tenants' tokens | `jwt` extra |
| [`oauth_state.py`](oauth_state.py) | One-time OAuth `state` values with `StateManager`, bound to the browser that started the flow | — |
| [`cache_lock.py`](cache_lock.py) | `CacheLock`, waiting (`async with`) and non-blocking (`acquire(blocking=False)`) | — |
| [`rate_limit.py`](rate_limit.py) | A fixed-window rate limiter on `backend.increment()`, answering `429` with `Retry-After` | — |
| [`redis_backend.py`](redis_backend.py) | `AsyncRedisCacheBackend` configured from environment variables in the lifespan and closed on shutdown | `redis` extra, a Redis server |

## Running an example

From a checkout of this repository:

```bash
uv sync --group dev
uv run --with "fastapi-cli[standard]" fastapi dev examples/http_cache.py
```

Then open <http://127.0.0.1:8000/docs>. The dev group already includes the
`jwt` and `redis` extras. `fastapi dev` comes from `fastapi-cli`, which is not
a dependency of this project, hence `--with`. Uvicorn works too:

```bash
uv run --with uvicorn uvicorn examples.http_cache:app --reload
```

`redis_backend.py` and `session_redis.py` read `REDIS_HOST`, `REDIS_PORT`,
`REDIS_DB` and `REDIS_PASSWORD`; point them at a server you can write to.

Some files mark parts with `# --8<-- [start:name]` / `# --8<-- [end:name]`
comments. The documentation site includes those parts (or the whole file) in
its guides, so the code shown there is the code tested here.

In your own project, install the extras an example needs, for example
`uv add "fastapi-cachex[jwt]"`, and copy the file.

## Secrets

The session examples read their signing key from `SESSION_SECRET_KEY`. When it
is unset they warn and sign with a random key made up for that run, so sessions
end when the process restarts and are not shared between workers. The
monitoring routes in `http_cache.py` stay closed until `CACHE_ADMIN_TOKEN` is
set. Always set real, random values outside local development:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Tests

`tests/test_examples.py` loads every example and drives its main flow, so the
examples keep working as the library changes. A new example needs a test
there and a row in the table above.
