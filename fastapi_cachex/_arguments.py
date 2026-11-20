"""Validation of the ``@cache`` arguments, and its warnings (#562)."""

import logging
import threading
from collections.abc import Callable
from collections.abc import Iterable
from datetime import timedelta

from fastapi import Request
from starlette.applications import Starlette
from starlette.routing import Router

from . import _tags
from ._callables import _is_coroutine_callable
from .backends.base import MAX_TTL
from .backends.base import bare_decorator_hint
from .exceptions import CacheXError
from .exceptions import TTLTypeError
from .exceptions import TTLValueError

# One logger for the decorator and the modules split from it.
logger = logging.getLogger("fastapi_cachex.cache")

_COOKIE_VARY_WARNING = (
    "cache vary on Cookie: @cache(vary=[...]) lists Cookie, so every distinct "
    "Cookie header gets its own entry and the number of entries grows with "
    "the number of visitors (and a new entry is made whenever any cookie "
    "changes). Key on the one value that matters instead, with a key_builder "
    "returning build_cache_key(request, <that cookie or the user id>), or use "
    "private=True. To keep vary=['Cookie'], silence this with "
    "warnings.filterwarnings('ignore', message='cache vary on Cookie')."
)


def _check_target(func: object) -> None:
    """Refuse an app or router: callable, so ``@cache`` took it and cached nothing (#581)."""
    if isinstance(func, (Starlette, Router)):
        msg = (
            f"@cache decorates a route function, not {type(func).__name__}: "
            "put it on each function, below its @app.get(...) or "
            "@router.get(...)"
        )
        raise CacheXError(msg)


def _no_store_ignored_warning(ignored: list[str]) -> str:
    return (
        f"cache no_store ignores {', '.join(ignored)}: @cache(no_store=True) "
        "stores nothing and sends only Cache-Control: no-store, so the other "
        "arguments have no effect. Remove them, or drop no_store."
    )


# How the one-time bypass warning names each `_request_credential` result.
_CREDENTIAL_DESCRIPTIONS = {
    "Authorization header": "an Authorization header",
    "Session data": "non-empty session data (request.session)",
}

_BYPASS_WARNING = (
    "@cache bypassed the shared backend for route %r: the request carried %s, "
    "so the response is not cached and is sent with Cache-Control: private. "
    "If the response is the same for every user, set @cache(public=True) "
    "(this also sends Cache-Control: public, so shared caches downstream may "
    "store it). If it is per user, put the verified caller's identity into "
    "the key with a key_builder (or list the header that carries it in vary) "
    "and set cache_authorized=True. Logged once per route and credential; "
    "each bypass is logged at DEBUG."
)

# A response whose `Vary` the key does not hold is served unstored (#632).
_UNKEYED_VARY_WARNING = (
    "@cache did not store the response of route %r: it is sent with %s, a "
    "header the cache key does not hold, so a stored copy would be served "
    "whatever the request's header says. Add the header to @cache(vary=...) "
    "to cache the route (Vary: * is never stored). Logged once per route and "
    "header; X-Cache-Reason: vary with debug_header=True."
)

# A `cache_authorized` route whose key does not tell the callers apart: it
# would serve one caller's response to the next, so it bypasses (#574).
_UNKEYED_CALLER_WARNING = (
    "@cache bypassed the shared backend for route %r: the request carried %s "
    "and the route sets cache_authorized=True, but the cache key does not "
    "identify the caller, so the response is not cached (it would be served "
    "to the next caller) and is sent with Cache-Control: private. Put the "
    "verified caller's identity into the key with a key_builder, or list the "
    "header that carries it in vary. To share one entry between all callers "
    "on purpose, pass key_builder=default_key_builder, or set public=True. "
    "Logged once per route and credential; each bypass is logged at DEBUG."
)


class _BypassWarner:
    """Log the credential bypass at ``WARNING`` once per route and credential.

    One instance lives in each ``@cache``-decorated function, so the state
    goes away with the function (and a test's fresh app starts with none).
    The route is keyed by its template (``/items/{item_id}``), never by the
    requested path, so the set stays bounded by the routes the function is
    registered on and client-chosen paths cannot grow it.
    """

    def __init__(self, fallback_name: str) -> None:
        self._fallback_name = fallback_name
        self._warned: set[tuple[str, str]] = set()
        # The wrapper runs on an event loop, where check-then-add cannot be
        # interleaved, but one decorated function can serve apps running on
        # several loops in different threads (several servers in one process,
        # each `TestClient`), and a set's check-then-add is not atomic across
        # threads. The lock keeps "once" exact; it costs nothing on the hot
        # path, which returns before taking it once the pair is recorded.
        self._lock = threading.Lock()

    def __call__(
        self, request: Request, credential: str, message: str = _BYPASS_WARNING
    ) -> None:
        route_path = getattr(request.scope.get("route"), "path", None)
        # Without a matched route (not reachable through FastAPI's router),
        # name the handler: the requested path is client input and unbounded.
        route = route_path if isinstance(route_path, str) else self._fallback_name
        pair = (route, credential)
        if pair in self._warned:
            return
        with self._lock:
            if pair in self._warned:
                return
            self._warned.add(pair)
        # Only the route template and the credential kind: never a header
        # value or token. `%r` escapes anything unusual in the template.
        logger.warning(
            message, route, _CREDENTIAL_DESCRIPTIONS.get(credential, credential)
        )


def _seconds(name: str, value: int | timedelta | None) -> int | None:
    """``value`` as a non-negative number of whole seconds, for ``@cache``.

    ``@cache`` allows ``0`` (``max-age=0``), which the backends refuse, so it
    does not use ``validate_ttl``; the type and range rules are the same.

    Raises:
        TTLTypeError: If ``value`` is not an ``int`` or a ``timedelta``
        TTLValueError: If ``value`` is negative, is larger than ``MAX_TTL`` or
            is a ``timedelta`` that is not a whole number of seconds

    Both are ``CacheXError`` subclasses, which ``@cache`` raised before, and
    the same classes the backends raise (#337).
    """
    if value is None:
        return None
    if isinstance(value, timedelta):
        if value.microseconds:
            msg = f"{name} must be a whole number of seconds, got {value!r}"
            raise TTLValueError(msg)
        value = int(value.total_seconds())
    elif isinstance(value, bool) or not isinstance(value, int):
        # Checked here: at request time the backend would reject it, and
        # failing open would hide that the route never caches.
        msg = (
            f"{name} must be an int number of seconds or a timedelta, "
            f"got {type(value).__name__}{bare_decorator_hint(value)}"
        )
        raise TTLTypeError(msg)
    if value < 0:
        msg = f"{name} must not be negative"
        raise TTLValueError(msg)
    if value > MAX_TTL:
        msg = f"{name} must be at most {MAX_TTL} seconds"
        raise TTLValueError(msg)
    return value


def _check_client_cache_control(value: object) -> None:
    """Reject a ``client_cache_control`` that is not a bool or a plain function."""
    if isinstance(value, bool):
        return
    if not callable(value):
        msg = (
            "client_cache_control must be a bool or a callable taking the "
            f"request, got {type(value).__name__}"
        )
        raise CacheXError(msg)
    if _is_coroutine_callable(value):
        # Its coroutine would never be awaited, and would count as "yes".
        msg = "client_cache_control must be a plain function, not async"
        raise CacheXError(msg)


def _check_tags(
    tags: object,
) -> tuple[str, ...] | Callable[[Request], Iterable[str]]:
    """The route's tags: a tuple of names, or the callable that gives them."""
    if callable(tags):
        if _is_coroutine_callable(tags):
            msg = "tags must be a plain function, not async"
            raise CacheXError(msg)
        return tags
    try:
        return _tags.validate_tags(tags)
    except (TypeError, ValueError) as e:
        raise CacheXError(str(e)) from e


def _check_serve_stale(
    serve_stale: object,
    *,
    ttl: int | None,
    stale: object,
    no_cache: bool,
    private: bool,
    must_revalidate: bool,
) -> None:
    """Reject a ``serve_stale`` route that could never serve a stale entry."""
    if not isinstance(serve_stale, bool):
        msg = f"serve_stale must be a bool, got {type(serve_stale).__name__}"
        raise CacheXError(msg)
    if not serve_stale:
        return
    if stale is None:
        msg = "serve_stale needs stale and stale_ttl: they say when and how long"
        raise CacheXError(msg)
    if private or no_cache or must_revalidate or not ttl:
        msg = (
            "serve_stale needs a positive ttl and cannot be combined with "
            "private, no_cache or must_revalidate, which never allow a stale "
            "response"
        )
        raise CacheXError(msg)


def _check_arguments(
    *,
    ttl: int | None,
    stale: object,
    stale_ttl: int | None,
    no_cache: bool,
    no_store: bool,
    public: bool,
    private: bool,
    coalesce: object,
    debug_header: object,
    client_cache_control: object,
    serve_stale: object,
    must_revalidate: bool,
    s_maxage: int | None,
    proxy_revalidate: bool,
) -> None:
    """Reject ``@cache`` arguments that contradict each other.

    Raises:
        CacheXError: See ``cache()``
    """
    if stale is not None and stale_ttl is None:
        msg = "stale_ttl must be set if stale is used"
        raise CacheXError(msg)
    if stale_ttl is not None and stale is None:
        msg = "stale must be set if stale_ttl is used"
        raise CacheXError(msg)
    if public and private:
        msg = "public and private are mutually exclusive"
        raise CacheXError(msg)
    for name, value in (("s_maxage", s_maxage), ("proxy_revalidate", proxy_revalidate)):
        # Identity, not equality: `s_maxage=0` is a value, and `0 == False`.
        if private and value is not None and value is not False:
            # Both speak to shared caches, which must not store a private
            # response at all.
            msg = (
                f"{name} cannot be combined with private: it only applies "
                "to shared caches, which do not store a private response"
            )
            raise CacheXError(msg)
    if not isinstance(coalesce, bool) and coalesce != "distributed":
        msg = f'coalesce must be a bool or "distributed", got {coalesce!r}'
        raise CacheXError(msg)
    if not isinstance(debug_header, bool):
        msg = f"debug_header must be a bool, got {type(debug_header).__name__}"
        raise CacheXError(msg)
    _check_client_cache_control(client_cache_control)
    if not no_store:
        _check_serve_stale(
            serve_stale,
            ttl=ttl,
            stale=stale,
            no_cache=no_cache,
            private=private,
            must_revalidate=must_revalidate,
        )
    if coalesce and not no_store and (private or no_cache or not ttl):
        # Such a route never serves a stored entry, so there would be
        # nothing to wait for.
        msg = (
            "coalesce needs a positive ttl and cannot be combined with "
            "private or no_cache, which never serve a stored entry"
        )
        raise CacheXError(msg)
