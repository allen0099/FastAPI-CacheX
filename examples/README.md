# Examples

Each file here is a complete FastAPI app that shows one feature of
FastAPI-CacheX. They use only the public API and the in-memory backend (except
`redis_backend.py`), so they run without any server.

| Example | What it shows | Needs |
|---------|---------------|-------|
| [`http_cache.py`](http_cache.py) | `@cache` with a TTL, ETag and `304 Not Modified`, `no_cache` and `private` routes, `clear_path()` after an update, the monitoring routes behind an admin check | — |
| [`app_cache.py`](app_cache.py) | `CacheManager.get_or_set()` for an expensive call, `add()` as an idempotency check, the `AppCache` dependency | — |
| [`session_login.py`](session_login.py) | `FastAPICacheXSessionMiddleware` with cookies: an anonymous session, login with `login()`, `AuthenticatedSession`, logout with `request.session.clear()` | — |
| [`session_jwt.py`](session_jwt.py) | Sessions with `token_format="jwt"` for API clients (`Authorization: Bearer`), revoked on logout | `jwt` extra |
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

`redis_backend.py` reads `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` and
`REDIS_PASSWORD`; point it at a server you can write to.

In your own project, install the extras an example needs, for example
`uv add "fastapi-cachex[jwt]"`, and copy the file.

## Secrets

The session examples read their signing key from `SESSION_SECRET_KEY` and fall
back to an obvious development placeholder. The monitoring routes in
`http_cache.py` stay closed until `CACHE_ADMIN_TOKEN` is set. Always set real,
random values outside local development:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Tests

`tests/test_examples.py` loads every example and drives its main flow, so the
examples keep working as the library changes. A new example needs a test
there and a row in the table above.
