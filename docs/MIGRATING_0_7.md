# Migrating to 0.7.0 {#migrating-to-070}

This page lists what 0.7.0 changes for code written for 0.6.x. Upgrade to 0.6.5 (the last 0.6.x) first and clear the warnings it shows; what remains is described here.

Releases come only from `master`, so 0.6.x gets no further releases, security fixes included, once the first 0.7.0 change is merged (see the [security policy](https://github.com/allen0099/FastAPI-CacheX/blob/master/SECURITY.md)).

## redis-py 7.4 or later {#redis-74}

0.7.0 requires redis-py 7.4 or later for the Redis backend ([#605](https://github.com/allen0099/FastAPI-CacheX/issues/605)). Before 7.4, a backend whose connection pool was first used on an event loop that has since closed never recovers: concurrent calls on a later loop raise `RuntimeError: ... is bound to a different event loop`, on that loop and every one after it. That happens to an app whose tests each run on a new loop, or that calls the backend from `asyncio.run()` more than once. From 7.4 the client drops the stale connection and reconnects.

0.6.5 says so first: creating a Redis backend with an older redis-py emits a `FutureWarning` once per process:

```text
FutureWarning: fastapi-cachex 0.7.0 will require redis-py 7.4 or later; redis-py 5.3.0 is installed. ...
```

The fix is to upgrade redis-py, which needs no change to your code:

```bash
pip install "redis[hiredis]>=7.4"
```

Until you can, a test suite that turns warnings into errors (`filterwarnings = ["error"]`) can ignore this one warning:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore:fastapi-cachex 0.7.0 will require redis-py 7.4:FutureWarning",
]
```

Outside pytest, call `warnings.filterwarnings("ignore", message="fastapi-cachex 0.7.0 will require redis-py 7.4", category=FutureWarning)` before the first backend is created, or set `PYTHONWARNINGS="ignore:fastapi-cachex 0.7.0 will require redis-py 7.4:FutureWarning"`.

## HTTP cache keys include the scheme {#key-scheme}

The key `@cache` builds for a response has no scheme: `http:v2|GET|example.com|/page|p=2` is the same for `http://example.com/page?p=2` and `https://example.com/page?p=2`. A response cached for a plain-http request is served to the next https request, so a body with absolute links built from the request URL (a pagination `next` link, for example) hands https clients `http://` links ([#576](https://github.com/allen0099/FastAPI-CacheX/issues/576)).

0.7.0 adds the scheme to the key and changes the format tag to `http:v3`, so http and https requests get separate entries. What it means for an upgrade:

- Entries written by 0.6.x are not read by 0.7.0: every route starts with a cold cache once. They expire with their `ttl`; to remove them at once on Redis, run `clear_pattern("http:v2|*")` after the upgrade (Memcached cannot enumerate keys, see [Choosing a backend](BACKENDS.md#choosing-a-backend)).
- During a rolling deploy, 0.6.x and 0.7.0 instances do not read each other's entries, and an invalidation from one version does not reach the other's entries. Keep each `ttl` in mind, or move every instance at once.
- The scheme is the one Starlette reports (`request.url.scheme`). Behind a proxy that terminates TLS, every request looks like `http` unless the server trusts the proxy's `X-Forwarded-Proto` (`uvicorn --proxy-headers --forwarded-allow-ips=...`); then nothing splits.
- A custom `key_builder` that calls `build_cache_key()` follows automatically. One that builds the key itself keeps its keys as they are.
- Code that reads keys from `/cached-hits` or `get_all_keys()` sees the new tag and the scheme field.

## `MemoryBackend.lock` and `start_cleanup()` are removed {#memory-backend}

Both were deprecated in 0.6.2 and warn with a `DeprecationWarning` on every use ([#504](https://github.com/allen0099/FastAPI-CacheX/issues/504)).

- `MemoryBackend.lock`: each operation is already atomic on one event loop, and the lock gave no thread safety. To make several operations one step, use [`CacheLock`](LOCK.md) or your own `asyncio.Lock`.
- `MemoryBackend.start_cleanup()`: the first read or write starts the cleanup task, so remove the call. `stop_cleanup()` stays.
