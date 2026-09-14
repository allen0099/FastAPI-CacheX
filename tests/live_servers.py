"""Opt-in gate for the suites that talk to a real Redis or Memcached.

These suites are destructive. Their fixtures call ``backend.clear()`` around
every test, which on Redis deletes every ``fastapi_cachex:``-prefixed key on
the server they connect to, and on Memcached issues ``flush_all`` -- the
Memcached protocol has no key enumeration, so there is no narrower option and
the *entire* server is wiped.

So there is deliberately **no default port**. Had the ports defaulted to 6379
and 11211, a plain ``uv run pytest`` on a machine that happens to run either
service locally would silently destroy the developer's own data -- and a
developer who runs Redis locally *and* has this library checked out is exactly
the person whose ``fastapi_cachex:`` keys are their own application's cache.
Naming the port explicitly is the developer stating that the server on it is
disposable.

To run these suites, point them at a throwaway container::

    ./scripts/start-redis-server.sh        # 6380
    ./scripts/start-memcache-server.sh     # 11212

    CACHEX_TEST_REDIS_PORT=6380 CACHEX_TEST_MEMCACHED_PORT=11212 uv run pytest

Without those variables the suites skip, which keeps ``uv run pytest`` safe to
run anywhere but means coverage of the network backends comes only from the
runs that opt in (CI sets both variables).
"""

import os
import socket

import pytest

REDIS_PORT_ENV = "CACHEX_TEST_REDIS_PORT"
REDIS_HOST_ENV = "CACHEX_TEST_REDIS_HOST"
MEMCACHED_PORT_ENV = "CACHEX_TEST_MEMCACHED_PORT"
MEMCACHED_HOST_ENV = "CACHEX_TEST_MEMCACHED_HOST"


def _explicit_port(env_var: str) -> int | None:
    """Return the port named by `env_var`, or None when it is unset/blank."""
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return None
    return int(raw)


# A port nothing of ours listens on. Used as the stand-in whenever no real port
# was named, so that a client can still be *constructed* (pymemcache parses the
# port at construction time, and a few tests only build a client and never
# connect) while any connection attempt goes nowhere instead of to 6379/11211.
UNCONNECTED_PORT = 1

REDIS_HOST = os.environ.get(REDIS_HOST_ENV, "127.0.0.1")
MEMCACHED_HOST = os.environ.get(MEMCACHED_HOST_ENV, "127.0.0.1")

_REDIS_PORT = _explicit_port(REDIS_PORT_ENV)
_MEMCACHED_PORT = _explicit_port(MEMCACHED_PORT_ENV)

REDIS_OPTED_IN = _REDIS_PORT is not None
MEMCACHED_OPTED_IN = _MEMCACHED_PORT is not None

REDIS_PORT: int = _REDIS_PORT if _REDIS_PORT is not None else UNCONNECTED_PORT
MEMCACHED_PORT: int = (
    _MEMCACHED_PORT if _MEMCACHED_PORT is not None else UNCONNECTED_PORT
)
MEMCACHED_SERVER = f"{MEMCACHED_HOST}:{MEMCACHED_PORT}"


def _port_is_open(host: str, port: int) -> bool:
    """Return True if something accepts a TCP connection on host:port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
    except OSError:
        # Covers a refused connection as well as a timeout: with nothing
        # listening the suite must skip, not fail during collection.
        return False
    else:
        return True
    finally:
        s.close()


def _opt_in_reason(
    service: str,
    env_var: str,
    host: str,
    port: int,
    *,
    opted_in: bool,
    docker_hint: str,
) -> str | None:
    """Return why `service` must be skipped, or None if it can be used."""
    if not opted_in:
        return (
            f"{service} tests are opt-in: they wipe the server they connect to, "
            f"so there is no default port. Start a throwaway instance "
            f"(`{docker_hint}`) and set {env_var} to its port."
        )
    if not _port_is_open(host, port):
        return f"{env_var}={port} is set but nothing is listening on {host}:{port}"
    return None


def has_redis_package() -> bool:
    """Return True if the `redis` package is importable."""
    try:
        import redis.asyncio  # type: ignore[unused-ignore]  # noqa: F401

    except Exception:
        return False
    return True


def redis_skip_reason() -> str | None:
    """Return why the Redis suites must be skipped, or None to run them."""
    return _opt_in_reason(
        "Redis",
        REDIS_PORT_ENV,
        REDIS_HOST,
        REDIS_PORT,
        opted_in=REDIS_OPTED_IN,
        docker_hint="./scripts/start-redis-server.sh",
    )


def memcached_skip_reason() -> str | None:
    """Return why the Memcached suites must be skipped, or None to run them."""
    return _opt_in_reason(
        "Memcached",
        MEMCACHED_PORT_ENV,
        MEMCACHED_HOST,
        MEMCACHED_PORT,
        opted_in=MEMCACHED_OPTED_IN,
        docker_hint="./scripts/start-memcache-server.sh",
    )


_REDIS_REASON = redis_skip_reason()
_MEMCACHED_REASON = memcached_skip_reason()

requires_redis = pytest.mark.skipif(
    _REDIS_REASON is not None,
    reason=_REDIS_REASON or "",
)
requires_memcached = pytest.mark.skipif(
    _MEMCACHED_REASON is not None,
    reason=_MEMCACHED_REASON or "",
)
requires_redis_package = pytest.mark.skipif(
    not has_redis_package(),
    reason="redis package is not installed",
)
