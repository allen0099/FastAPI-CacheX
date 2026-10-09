# Migrating to 0.5.0 {#migrating-to-050}

0.5.0 contains breaking changes, collected in the [0.5.0 milestone](https://github.com/allen0099/FastAPI-CacheX/issues?q=milestone%3A0.5.0). This page lists every one of them, what to change, and whether 0.4.x already warns about it. The largest is the removal of sessions and OAuth state, which 0.4.0 deprecated.

## Before you upgrade {#before-you-upgrade}

Upgrade to the latest 0.4.x release first and run your test suite with the library's warnings turned into errors:

```bash
python -W error::FutureWarning -m pytest
```

or, with pytest's own setting:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error::FutureWarning",
]
```

0.4.x emits a `FutureWarning` when `fastapi_cachex.session` or `fastapi_cachex.state` is imported, and when a third-party backend's `delete()` returns `None`. Once 0.4.x runs without them, the changes with a warning in the "Warned in 0.4.x" column are done; the rest of this page covers what no warning can detect.

## Summary {#summary}

| Change | Issue | Warned in 0.4.x | Section |
|--------|-------|-----------------|---------|
| Sessions and OAuth state removed | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | `FutureWarning` | [Sessions and OAuth state](#session-state-removed) |
| `jwt` extra removed | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | No | [jwt extra](#jwt-extra) |
| `@cache` no longer recognises sessions loaded by the removed middleware | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | No | [Session tokens and @cache](#cache-session-token) |
| `itsdangerous` and `starlette` are no longer direct requirements; lower `fastapi` floor | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | No | [Dependencies](#dependencies) |
| A backend `delete()` that returns `None` counts as not removed | [#421](https://github.com/allen0099/FastAPI-CacheX/issues/421) | `FutureWarning` | [delete() returning None](#backend-delete-none) |

## Sessions and OAuth state are removed {#session-state-removed}

`fastapi_cachex.session` and `fastapi_cachex.state`, deprecated in 0.4.0 ([#420](https://github.com/allen0099/FastAPI-CacheX/issues/420)), are removed ([#421](https://github.com/allen0099/FastAPI-CacheX/issues/421)). That takes with it `FastAPICacheXSessionMiddleware`, `SessionManager`, `SessionConfig`, the session dependencies (`AuthenticatedSession`, `OptionalSession`, `get_session` and the rest), the token serializers, `StateManager`, their proxies and their exceptions. Importing either package now raises `ModuleNotFoundError`, and reading one of the names from `fastapi_cachex` (such as `fastapi_cachex.SessionConfig`) raises `AttributeError`; neither warns first any more.

Where to move is unchanged from 0.4.0: see [Sessions and OAuth state are deprecated](MIGRATING_0_4.md#session-state-deprecated) in the 0.4.0 guide. In short, Starlette's `SessionMiddleware` for signed-cookie sessions, a server-side session library for sessions that must live on the server, the access tokens of your authentication stack for APIs, and your OAuth client library for `state`. `@cache`, `CacheManager`, `CacheLock` and the backends are not affected.

Sessions and states already in the backend need no cleanup; they expire on their own TTL. To remove them at once on Redis or the memory backend, clear their prefixes (the defaults were `session:` and `oauth_state:`):

```python
await backend.clear_pattern("session:*")
await backend.clear_pattern("oauth_state:*")
```

Check first that none of your own keys starts with either prefix. Memcached cannot enumerate keys, so there they just expire.

The 0.4.x documentation of the removed packages stays readable in the repository at the [v0.4.1 tag](https://github.com/allen0099/FastAPI-CacheX/tree/v0.4.1/docs).

### jwt extra {#jwt-extra}

The `jwt` extra only pulled in `PyJWT` for `SessionConfig(token_format="jwt")`, so it is removed. `uv add "fastapi-cachex[jwt]"` (or pip) now only warns about an unknown extra and installs without `PyJWT`. If your own code uses `PyJWT`, depend on it directly:

```bash
# Before
uv add "fastapi-cachex[redis,jwt]"

# After
uv add "fastapi-cachex[redis]" PyJWT
```

## Session tokens and @cache {#cache-session-token}

`@cache` bypasses the shared backend for a request with credentials, and sends its answer with `private` (see [HTTP caching](HTTP_CACHING.md#authenticated-endpoints)). Two kinds of credential are left:

- an `Authorization` header;
- a non-empty `request.session`, from any session middleware, such as Starlette's `SessionMiddleware`.

What 0.4.x also counted, and 0.5.0 no longer does:

- A session that `FastAPICacheXSessionMiddleware` loaded, from its `X-Session-Token` header, a bearer token or its cookie, even with no data in it. The middleware is gone, so the check went with it.

A `Cookie` header on its own still does not bypass the cache, as before. `X-Session-Token` in `@cache(vary=[...])` is still keyed on a SHA-256 digest of its value, like `Authorization`, `Proxy-Authorization` and `Cookie`, so keys do not change. If your replacement for the session middleware identifies callers by a header or cookie that `@cache` cannot see, a plain `@cache` on such a route serves the first caller's response to everyone. Use `private=True`, or a `key_builder` that puts the verified identity into the key together with `cache_authorized=True`, and, for a custom token header, key on it through the `key_builder` (hashing it yourself) rather than `vary`:

```python
# Before: a session the middleware loaded from X-Session-Token bypassed the backend
@cache(ttl=60, vary=["X-Session-Token"], cache_authorized=True)

# After: hash the token yourself, or better, key on the verified user id
def per_user_key(request: Request) -> str:
    return build_cache_key(request, request.state.user_id)


@cache(ttl=60, key_builder=per_user_key, cache_authorized=True)
```

0.4.x does not warn: it cannot tell what will replace the session middleware.

## Dependencies {#dependencies}

The core needs fewer and older packages ([#421](https://github.com/allen0099/FastAPI-CacheX/issues/421)):

- `itsdangerous` is no longer a requirement. Only the session middleware needed it. If you move to Starlette's `SessionMiddleware`, which imports it, add `itsdangerous` to your own dependencies.
- `starlette` is no longer a direct requirement. 0.4.x required `starlette>=1.0.0` for the session middleware; 0.5.0 uses whichever version your `fastapi` allows.
- The `fastapi` floor drops from `0.133.0` to `0.128.2`, the oldest release the test suite passes on (with the oldest Starlette it accepts, 0.40.0). `pydantic>=2.7.0` is unchanged.

Nothing needs to change unless something else in your project relied on `fastapi-cachex` to install `itsdangerous` or a recent `starlette`; pin those yourself.

## delete() returning None {#backend-delete-none}

Since 0.4.0, `BaseCacheBackend.delete()` returns whether the key was removed (see [delete() return value](MIGRATING_0_4.md#backend-delete)). The non-atomic fallbacks on the base class (`delete_many()`, `get_and_delete()` and `delete_if_equals()`) still counted a `None` from a third-party `delete()` as removed, as 0.3.x did, and emitted a `FutureWarning`. 0.5.0 reads the result with `bool()`, so `None` now counts as not removed, without a warning: `delete_many()` returns `0` for it, and `get_and_delete()` and `delete_if_equals()` report that the caller did not win, although the key is gone. `CacheManager.delete()` is built on `get_and_delete()`, so it returns `False` too.

Built-in backends are not affected. A third-party backend fixes this by returning a `bool`:

```python
# Before
class MyBackend(BaseCacheBackend):
    async def delete(self, key: str) -> None:
        await self._client.delete(key)


# After
class MyBackend(BaseCacheBackend):
    async def delete(self, key: str) -> bool:
        return await self._client.delete(key) > 0
```

0.4.x emits a `FutureWarning` whenever a fallback receives `None`.
