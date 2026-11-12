"""How one request to an ``@cache`` route is answered (split from ``cache.py``, #562).

``_Route`` holds what the decorator decided for a handler, ``_Call`` one
request to it.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal

from fastapi import BackgroundTasks
from fastapi import Request
from fastapi import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import _tags
from ._arguments import _BypassWarner
from ._cache_control import _NO_STORE
from ._cache_control import _cache_control_for
from ._cache_control import _unshareable_reason
from ._cache_control import _with_cache_control
from ._coalesce import _claim_refresh
from ._coalesce import _Flight
from ._coalesce import _release_refresh
from ._coalesce import _wait_for
from ._key_builders import _append_key_components
from ._key_builders import _build_key
from ._rendering import HandlerCallable
from ._rendering import _dependency_unshareable_reason
from ._rendering import _render
from ._rendering import _respond
from ._stored_response import _UNCACHEABLE_HEADERS
from ._stored_response import _age_headers
from ._stored_response import _append_headers
from ._stored_response import _entry_age
from ._stored_response import _entry_for
from ._stored_response import _etag_matches
from ._stored_response import _fresh_not_modified
from ._stored_response import _is_cacheable_status
from ._stored_response import _not_modified
from ._vary import VaryNormaliser
from ._vary import _vary_components
from .backends.base import BaseCacheBackend
from .events import CacheReason
from .events import _emit
from .exceptions import CacheXError
from .lock import CacheLock
from .switch import is_enabled
from .types import CacheEntry
from .types import CacheKeyBuilder
from .types import log_ref

# One logger for the decorator and the modules split from it.
logger = logging.getLogger("fastapi_cachex.cache")

# `coalesce="distributed"`: the lock a rendering request holds renews itself
# every third of this, and a worker that died frees it within it. A request
# in another worker polls for the lock to go, backing off between the two
# delays.
_LOCK_TTL = 30
_LOCK_POLL_MIN = 0.05
_LOCK_POLL_MAX = 0.5
# The longest a request waits for another worker before it renders itself:
# the lock renews for as long as its holder's handler runs, so a handler
# that hangs must not hang the same URL in every worker.
_LOCK_WAIT_MAX = 30.0


# Methods `@cache` answers; HEAD shares the GET entry (#253).
_CACHED_METHODS = frozenset({"GET", "HEAD"})

# Sent as `X-Cache` with `debug_header=True` (#335).
_DEBUG_HEADER = "X-Cache"
# Sent beside it when the request did not use the cache as a route normally
# does: why the backend was bypassed, or why a miss was not stored.
_REASON_HEADER = "X-Cache-Reason"
_HIT = "HIT"
_MISS = "MISS"
_BYPASS = "BYPASS"
_REFRESH = "REFRESH"
_STALE = "STALE"

# From here up a response is a failure that `stale="error"` may answer from a
# stale entry (RFC 5861 §4 names 500, 502, 503 and 504).
_SERVER_ERROR = 500

# What a request's own `Cache-Control` may ask for on a route that allows it
# (`client_cache_control`, #541).
_ClientCacheControl = bool | Callable[[Request], bool]


def _client_directive(request: Request) -> str | None:
    """``"no-store"``, ``"no-cache"`` or None, from the request's ``Cache-Control``.

    ``max-age=0`` asks for the same as ``no-cache``: a response no older than
    now. Every other request directive is ignored.
    """
    directives = {
        directive.strip().lower().replace(" ", "")
        # Every line of the header: a client or a proxy may send several.
        for line in request.headers.getlist("cache-control")
        for directive in line.split(",")
    }
    if "no-store" in directives:
        return "no-store"
    if directives & {"no-cache", "max-age=0"}:
        return "no-cache"
    return None


def _as_get(request: Request) -> Request:
    """The same request with method GET, to build the key a GET would get.

    ``state`` and the session live in the scope, so a key builder that reads
    them sees what the HEAD request carries.
    """
    return Request({**request.scope, "method": "GET"}, request.receive)


def _log_backend_failure(
    what: str, request: Request, cache_key: str, error: Exception
) -> None:
    """Log a failed backend call without writing the cache key at WARNING.

    The key holds the raw query string, ``vary`` header values and any custom
    key components, so the warning carries the method, the path and a digest
    of the key; the full key goes to ``DEBUG`` under the same digest. The path
    is formatted with ``%r`` because it is percent-decoded client input and
    could otherwise put a CR/LF into the log.
    """
    key_ref = log_ref(cache_key)
    logger.warning(
        "Cache backend %s. method=%s path=%r key_ref=%s error=%r",
        what,
        request.method,
        request.url.path,
        key_ref,
        error,
    )
    logger.debug("Cache backend %s; key_ref=%s key=%s", what, key_ref, cache_key)
    _emit("error", "http", cache_key, route=_route_template(request))


def _route_template(request: Request) -> str | None:
    """The path template of the route answering ``request``, if it has one.

    The whole path, as a label that does not grow with the data:
    ``/api/users/{user_id}``. The matched route only knows its own part when
    it was included with a prefix or lives in a mounted application, so what
    precedes that part in the request's path is put back in front of it, with
    the values of any path parameters there replaced by their names.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    path_format = getattr(route, "path_format", None)
    if not isinstance(template, str) or not isinstance(path_format, str):
        return None
    params = request.path_params
    try:
        own = path_format.format(**params)
    except (KeyError, IndexError, ValueError):
        return template
    path = request.url.path
    if own == path or not path.endswith(own):
        return template
    prefix = path[: len(path) - len(own)]
    for name, value in params.items():
        if f"{{{name}" not in path_format:
            prefix = prefix.replace(f"/{value}", f"/{{{name}}}", 1)
    return prefix + template


async def _read_entry(
    backend: BaseCacheBackend, cache_key: str, request: Request, *, fail_open: bool
) -> tuple[CacheEntry | None, bool]:
    """Read the entry under ``cache_key`` and whether the read worked.

    A failed read is a miss if failing open.
    """
    try:
        entry = await backend.get(cache_key)
        if (
            entry is not None
            and entry.tags
            and await _tags.outdated(backend, {cache_key: entry})
        ):
            logger.debug("A tag of the entry was invalidated; key=%s", cache_key)
            entry = None
    except Exception as e:
        if not fail_open:
            _emit("error", "http", cache_key, route=_route_template(request))
            raise
        _log_backend_failure("read failed; serving uncached", request, cache_key, e)
        return None, False
    return entry, True


def _request_credential(request: Request) -> str | None:
    """What identifies the caller of ``request``, or ``None`` if nothing does.

    ``Authorization``, or a non-empty ``request.session`` from any session
    middleware (Starlette's ``SessionMiddleware``, for example). An empty
    session does not count, and neither does a ``Cookie`` header alone.
    """
    if "authorization" in request.headers:
        return "Authorization header"
    if request.scope.get("session"):
        return "Session data"
    return None


@dataclass
class _Call:
    """One GET, HEAD or other request to a decorated handler.

    ``dependency_lines`` and ``dependency_status`` are what the dependencies
    left on the sub-response before the handler ran (see
    ``_split_header_lines``). ``outcome`` is how the request was answered,
    for the ``X-Cache`` header: ``HIT`` (served from the stored entry, as a
    200 or a 304 for its ETag), ``MISS`` (the backend was consulted and the
    handler ran), ``BYPASS`` (the backend was neither read nor written),
    ``REFRESH`` (the request skipped the entry, see ``client_cache_control``),
    ``STALE`` (an entry past its ttl was served, see ``serve_stale``), or
    ``None`` for a method that is not cached.
    """

    request: Request
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    sub_response: Response | None
    dependency_lines: list[tuple[bytes, bytes]]
    dependency_status: int | None
    background: BackgroundTasks | None = None
    cache_key: str | None = None
    # The versions of the route's tags, read before the handler ran. None
    # until then, and when they could not be read: a response of a tagged
    # route is not stored without them.
    tags_before: _tags.VersionsBefore | None = None
    tag_names: tuple[str, ...] | None = None
    flight: _Flight = field(default_factory=_Flight)
    # The lock this request holds across workers while it renders
    # (`coalesce="distributed"`); the wrapper releases it.
    lock: CacheLock | None = None
    outcome: str | None = None
    # Why the backend was bypassed or the rendered response was not stored.
    reason: CacheReason | None = None

    @property
    def client_etag(self) -> str | None:
        return self.request.headers.get("if-none-match")

    async def finish(self) -> None:
        """The request has been answered, however it ended."""
        self.flight.finish()
        if self.lock is not None:
            await _release_lock(self.lock)


@dataclass(frozen=True, eq=False)
class _Route:
    """What ``@cache(...)`` decided for one handler, and how it answers.

    ``cache_control`` only depends on the decorator arguments, so it is
    built once. ``private_cache_control`` is sent instead for a response
    that must not be stored downstream either: one that sets a cookie, or
    one answering a request with credentials.
    """

    func: HandlerCallable
    ttl: int | None
    stale: Literal["error", "revalidate"] | None
    stale_ttl: int | None
    serve_stale: bool
    no_cache: bool
    no_store: bool
    public: bool
    private: bool
    fail_open: bool
    cache_authorized: bool
    coalesce: bool | Literal["distributed"]
    client_cache_control: _ClientCacheControl
    tags: tuple[str, ...] | Callable[[Request], Iterable[str]]
    key_builder: CacheKeyBuilder
    vary_names: dict[str, VaryNormaliser | None]
    cache_control: str
    private_cache_control: str
    warn_bypass: _BypassWarner

    @property
    def skips_backend(self) -> bool:
        """Whether no request to this route reads or writes the backend.

        Without a positive ttl nothing may be served from storage, and a 304
        answered from a stored ETag would be exactly that: it would keep
        confirming a copy that the handler no longer produces (#110). Such
        routes skip the backend like private ones. ``ttl=0`` is included,
        since ``max-age=0`` allows no reuse either.
        """
        return self.private or not self.ttl

    @property
    def stored_ttl(self) -> int | None:
        """How long the backend keeps an entry.

        With ``serve_stale`` it outlives ``ttl`` by ``stale_ttl``, the time
        in which it may still be served stale.
        """
        if self.serve_stale and self.ttl is not None and self.stale_ttl is not None:
            return self.ttl + self.stale_ttl
        return self.ttl

    def _is_stale(self, entry: CacheEntry) -> bool:
        """Whether ``entry`` is past its ttl and kept only to be served stale.

        Without ``serve_stale`` the backend drops an entry at its ttl, so one
        that is read is fresh. An entry without ``stored_at`` (written by an
        older release, or outside ``@cache``) is taken as fresh.
        """
        if not self.serve_stale or self.ttl is None:
            return False
        age = _entry_age(entry)
        return age is not None and age >= self.ttl

    def _is_gone(self, entry: CacheEntry) -> bool:
        """Whether a stale ``entry`` is past the time it may be served at all.

        The backend drops it by then on its own clock; this one is the clock
        ``stored_at`` was written with, which may be another host's.
        """
        age = _entry_age(entry)
        return (
            self.serve_stale
            and age is not None
            and self.stored_ttl is not None
            and age >= self.stored_ttl
        )

    async def respond(self, call: _Call, backend: BaseCacheBackend) -> Response:
        """Answer ``call``, from the backend where the route and request allow."""
        req = call.request
        if req.method not in _CACHED_METHODS:
            logger.debug("Not GET or HEAD; bypassing cache for method=%s", req.method)
            return await self._run(call)
        if self.no_store:
            return await self._no_store(call)

        # RFC 9111 §3.5: a shared cache must not reuse a response to a
        # request with `Authorization` unless the response allows it. The
        # default key carries no identity, so treat such requests as
        # private unless the route is `public` or opted in. A request with
        # a non-empty `request.session` is the same case (#319). Routes
        # without a positive ttl skip the backend anyway, but their
        # response still needs `private` for a downstream cache (#362);
        # only `private=True` already sends it.
        # `cache_authorized` lifts the bypass but not `private`: its entries
        # are per caller only in this backend, while a shared cache
        # downstream keys on the URL alone (#372).
        credential = None if self.private or self.public else _request_credential(req)
        cache_control = (
            self.cache_control if credential is None else self.private_cache_control
        )
        authorized_bypass = credential is not None and not self.cache_authorized
        if credential is not None and not self.cache_authorized:
            logger.debug(
                "%s present; bypassing the backend for path=%s",
                credential,
                req.url.path,
            )
            # The warning is about lost cache hits, which a route that
            # never reads the backend does not have.
            if not self.skips_backend:
                self.warn_bypass(req, credential)

        if self.skips_backend or authorized_bypass:
            call.reason = (
                "private"
                if self.private
                else "no-ttl"
                if not self.ttl
                else "authorization"
                if "authorization" in req.headers
                else "session"
            )
            return await self._uncached(call, cache_control)
        if not is_enabled():
            # After the checks above, so that a request gets the same
            # `Cache-Control` as with the switch on.
            call.reason = "disabled"
            return await self._uncached(call, cache_control)
        asked = self._client_asks(req)
        if asked == "no-store":
            logger.debug("Request no-store; bypassing for path=%s", req.url.path)
            call.reason = "request-no-store"
            return await self._uncached(call, cache_control)
        return await self._cached(
            call, backend, cache_control, refresh=asked == "no-cache"
        )

    def _client_asks(self, request: Request) -> str | None:
        """What the request's ``Cache-Control`` asks for, if this route listens."""
        allowed = self.client_cache_control
        if allowed is False:
            return None
        asked = _client_directive(request)
        # `is True`: anything else a callable returns, a coroutine or a
        # non-empty string included, must not open the route.
        if asked is None or (allowed is not True and allowed(request) is not True):
            return None
        return asked

    async def _run(self, call: _Call) -> Response:
        """Run the handler and build its response."""
        return await _respond(
            self.func,
            call.request,
            call.args,
            call.kwargs,
            sub_response=call.sub_response,
            dependency_lines=call.dependency_lines,
        )

    async def _render(self, call: _Call) -> tuple[Response, bytes | None, str | None]:
        """Run the handler; the body and ETag are None for a streamed response."""
        return await _render(
            self.func,
            call.request,
            call.args,
            call.kwargs,
            sub_response=call.sub_response,
            dependency_lines=call.dependency_lines,
        )

    def _send(self, response: Response, cache_control: str) -> Response:
        """A freshly rendered response, with the ``Cache-Control`` it gets."""
        return _with_cache_control(response, cache_control, self.private_cache_control)

    def _revalidated(
        self, call: _Call, response: Response, etag: str, cache_control: str
    ) -> Response | None:
        """The 304 for a fresh render the client's ETag matches, else None."""
        return _fresh_not_modified(
            response,
            etag,
            call.client_etag,
            _cache_control_for(response, cache_control, self.private_cache_control),
        )

    async def _no_store(self, call: _Call) -> Response:
        """``no_store=True``: the highest priority, nothing else applies."""
        call.outcome = _BYPASS
        call.reason = "no-store"
        response = await self._run(call)
        logger.debug(
            "no-store active; bypassed cache for path=%s", call.request.url.path
        )
        # Unconditional: `no-store` is stricter than anything the
        # handler may have sent.
        response.headers["Cache-Control"] = _NO_STORE
        return response

    async def _uncached(self, call: _Call, cache_control: str) -> Response:
        """Answer without reading or writing the backend.

        A private response belongs to exactly one user, so it must never
        be read from or written to the shared backend: the default cache
        key carries no identity, so a stored copy would be served to the
        next caller. The same path serves routes without a positive ttl
        (see ``skips_backend``). ETag revalidation still works: it compares
        the client's validator against freshly rendered content.

        ``cache_control`` has ``private`` for a request with credentials:
        RFC 9111 §3.5 would still let a downstream shared cache reuse the
        answer to an ``Authorization`` request under ``must-revalidate``,
        and nothing at all stops one reusing the answer to a cookie.
        """
        call.outcome = _BYPASS
        path = call.request.url.path
        response, _, etag = await self._render(call)
        if not _is_cacheable_status(response.status_code):
            return response
        if etag is None:
            # StreamingResponse/FileResponse — cannot compute ETag
            return self._send(response, cache_control)
        not_modified = self._revalidated(call, response, etag, cache_control)
        if not_modified is not None:
            logger.debug("304 Not Modified (uncached); path=%s", path)
            return not_modified
        response.headers["ETag"] = etag
        logger.debug("Bypassed the backend; path=%s", path)
        return self._send(response, cache_control)

    async def _cached(
        self,
        call: _Call,
        backend: BaseCacheBackend,
        cache_control: str,
        *,
        refresh: bool = False,
    ) -> Response:
        """Answer from the stored entry, or render, store and answer.

        ``refresh`` skips the stored entry: the client asked for a response
        that is not from the cache, and the route allows that. The new
        response replaces the entry.
        """
        req = call.request
        # Built only here: the paths above never touch the backend, so a
        # custom key builder would run for nothing.
        # HEAD reads the entry of the GET (#253), whichever builder made
        # the key.
        cache_key = _append_key_components(
            _build_key(self.key_builder, _as_get(req) if req.method == "HEAD" else req),
            _vary_components(req, self.vary_names),
        )

        call.cache_key = cache_key
        # Decided below: a hit is served from the entry, anything else runs
        # the handler.
        call.outcome = _REFRESH if refresh else _MISS
        if refresh:
            entry = None
            await self._after_running_miss(call, backend, cache_key)
        else:
            entry = await self._read(call, backend, cache_key)
        stale = entry is not None and self._is_stale(entry)
        if entry is not None and not self.no_cache and not stale:
            return self._hit(call, entry, cache_key, cache_control)
        if (
            entry is not None
            and stale
            and self.stale == "revalidate"
            and self._refresh_later(call, backend, cache_key)
        ):
            # Answer now, from the old entry; the handler runs afterwards.
            return self._hit(call, entry, cache_key, cache_control, outcome=_STALE)

        await self._read_tag_versions(call, backend, cache_key)
        fallback = entry if stale and self.stale == "error" else None
        rendered = await self._render_unless_failing(call, fallback)
        if rendered is None:
            # Only with a fallback: the handler failed, the stale entry answers.
            assert fallback is not None  # noqa: S101 - for the type checker
            return self._hit(call, fallback, cache_key, cache_control, outcome=_STALE)
        return await self._fresh(
            call, backend, cache_key, cache_control, stored=entry, rendered=rendered
        )

    async def _fresh(
        self,
        call: _Call,
        backend: BaseCacheBackend,
        cache_key: str,
        cache_control: str,
        *,
        stored: CacheEntry | None,
        rendered: tuple[Response, bytes | None, str | None],
    ) -> Response:
        """Store what the handler just rendered, and answer with it."""
        entry = stored
        response, body, etag = rendered
        if not _is_cacheable_status(response.status_code):
            # Leave any existing entry alone: a transient error must not
            # evict or overwrite the last good response. Error responses
            # carry no validator either: never answer 304.
            logger.debug(
                "Uncacheable status %s; not storing key=%s",
                response.status_code,
                cache_key,
            )
            call.reason = "status"
            return response
        if body is None or etag is None:
            # StreamingResponse/FileResponse — cannot compute ETag; serve as-is
            call.reason = "streamed"
            return self._send(response, cache_control)

        if self.no_cache and call.client_etag:
            # A `no_cache` route answers a matching ETag before storing, so
            # that 304 leaves the stored entry as it is.
            not_modified = self._revalidated(call, response, etag, cache_control)
            if not_modified is not None:
                logger.debug("304 Not Modified via no-cache; key=%s", cache_key)
                return not_modified
        else:
            logger.debug("Cache MISS; computed fresh ETag for key=%s", cache_key)

        response.headers["ETag"] = etag
        await self._store(
            call,
            backend,
            cache_key,
            stored=entry,
            response=response,
            etag=etag,
            body=body,
        )

        # A client revalidating a copy that the fresh render reproduces gets
        # a 304, as on the bypass and no_cache paths: the entry may have
        # expired, been cleared or evicted, or never been stored in this
        # worker, while the client's ETag still matches (#237).
        not_modified = self._revalidated(call, response, etag, cache_control)
        if not_modified is not None:
            logger.debug("304 Not Modified (fresh render); key=%s", cache_key)
            return not_modified
        return self._send(response, cache_control)

    def _tag_names(self, call: _Call) -> tuple[str, ...]:
        """The route's tags for this request; a tags function runs once."""
        if call.tag_names is None:
            if callable(self.tags):
                try:
                    call.tag_names = _tags.validate_tags(self.tags(call.request))
                except (TypeError, ValueError) as e:
                    msg = f"the tags function of this route returned a bad value: {e}"
                    raise CacheXError(msg) from e
            else:
                call.tag_names = self.tags
        return call.tag_names

    def _usable(self, call: _Call, entry: CacheEntry | None) -> CacheEntry | None:
        """``entry``, unless it is too old to serve or lacks the route's tags.

        An entry stored before the route had these tags (before ``tags=`` was
        added, or under other names) could not be invalidated by them, so it
        is not served: the handler runs and stores it again, tagged.
        """
        if entry is None or self._is_gone(entry):
            return None
        if self.tags and {name for name, _ in entry.tags} != set(self._tag_names(call)):
            logger.debug("Entry lacks the route's tags; path=%s", call.request.url.path)
            return None
        return entry

    async def _read_tag_versions(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> None:
        """Note the versions of the route's tags, before the handler runs.

        Before, not after: a response rendered from data that an
        invalidation has since replaced must not be stored under the version
        that invalidation set.
        """
        names = self._tag_names(call)
        if not names:
            call.tags_before = {}
            return
        try:
            call.tags_before = await _tags.versions_before(backend, names)
        except Exception as e:
            if not self.fail_open:
                raise
            _log_backend_failure(
                "tag read failed; response not stored", call.request, cache_key, e
            )

    def _refresh_later(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> bool:
        """Arrange for a stale entry to be replaced after this response.

        A GET adds the refresh to the request's background tasks; a HEAD is
        just answered, since its response is never stored. False only when
        the request has no ``BackgroundTasks`` to run it on (the wrapper was
        not called by FastAPI), in which case the caller renders at once.
        """
        if call.background is None:
            return False
        if call.request.method == "GET":
            call.background.add_task(self._refresh, call, backend, cache_key)
        return True

    async def _refresh(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> None:
        """Replace a stale entry, after the response it answered was sent.

        A background task of the request: its dependencies are still open.
        One refresh per key and process runs at a time. Nothing is left to
        answer, so a failure is logged and the stale entry stays until a
        later request refreshes it or it expires.
        """
        if not _claim_refresh(backend, cache_key):
            return
        lock = None
        try:
            if self.coalesce == "distributed":
                lock = _miss_lock(backend, cache_key)
                if not await lock.acquire(blocking=False):
                    # Another worker is refreshing the key.
                    lock = None
                    return
            await self._replace_stale(call, backend, cache_key)
        except Exception as e:  # noqa: BLE001 - there is no response to fail
            logger.warning(
                "Refreshing the stale entry failed (%s: %s); path=%s",
                type(e).__name__,
                e,
                call.request.url.path,
            )
        finally:
            _release_refresh(backend, cache_key)
            if lock is not None:
                await _release_lock(lock)

    async def _replace_stale(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> None:
        """Run the handler and store its response, unless the entry is fresh."""
        entry, _ = await _read_entry(backend, cache_key, call.request, fail_open=True)
        entry = self._usable(call, entry)
        if entry is not None and not self._is_stale(entry):
            # A request served stale just before this one already replaced it.
            return
        await self._read_tag_versions(call, backend, cache_key)
        response, body, etag = await self._render(call)
        reason: str | None = self._skip_reason(call, response)
        if (
            body is None
            or etag is None
            or not _is_cacheable_status(response.status_code)
        ):
            reason = f"status {response.status_code}, or a streamed body"
        if reason is None:
            await self._store(
                call,
                backend,
                cache_key,
                stored=None,
                response=response,
                etag=etag,  # type: ignore[arg-type]
                body=body,  # type: ignore[arg-type]
            )
        else:
            logger.warning(
                "Refresh not stored (%s); the stale entry stays; path=%s",
                reason,
                call.request.url.path,
            )
        if response.background is not None:
            # The handler's own task: nothing sends this response to run it.
            await response.background()

    def _is_failure(self, request: Request, error: Exception) -> bool:
        """Whether ``error`` is a failure a stale entry may answer for.

        An ``HTTPException`` below 500 is the handler's answer. So is an
        exception the application has registered a handler for: it turns it
        into a response of its own choosing, often a 4xx.
        """
        if isinstance(error, StarletteHTTPException):
            return error.status_code >= _SERVER_ERROR
        handlers = getattr(request.app, "exception_handlers", {})
        return not any(
            cls in handlers for cls in type(error).__mro__ if cls is not Exception
        )

    async def _render_unless_failing(
        self, call: _Call, fallback: CacheEntry | None
    ) -> tuple[Response, bytes | None, str | None] | None:
        """Run the handler; None if it failed and ``fallback`` can answer.

        ``fallback`` is the stale entry of a ``stale="error"`` route. A
        failure is a 5xx response or an exception, other than the ones
        ``_is_failure`` takes as the handler's answer.
        """
        try:
            rendered = await self._render(call)
        except Exception as e:
            if fallback is None or not self._is_failure(call.request, e):
                raise
            # One line, no traceback: a failing upstream fails every request.
            logger.warning(
                "Handler failed (%s: %s); serving the stale entry; path=%s",
                type(e).__name__,
                e,
                call.request.url.path,
            )
            return None
        if fallback is not None and rendered[0].status_code >= _SERVER_ERROR:
            logger.warning(
                "Handler answered %s; serving the stale entry; path=%s",
                rendered[0].status_code,
                call.request.url.path,
            )
            return None
        return rendered

    async def _after_running_miss(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> None:
        """Let a coalesced miss that is running for the key finish first.

        A refresh renders after it, so the newer response is the one left in
        the backend. Without a running miss a GET leads, and misses arriving
        meanwhile wait for what it stores.
        """
        if not self.coalesce:
            return
        leader = call.flight.join(
            backend, cache_key, may_lead=call.request.method == "GET"
        )
        if leader is not None:
            await _wait_for(leader)

    async def _read(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> CacheEntry | None:
        """The usable stored entry, after waiting for a running miss if coalescing.

        An entry the route cannot serve (see ``_usable``) is a miss here
        already, so that concurrent requests for it are coalesced like any
        other miss.
        """
        req = call.request
        entry, read_ok = await _read_entry(
            backend, cache_key, req, fail_open=self.fail_open
        )
        entry = self._usable(call, entry)
        # A failed read does not wait: the backend is likely down, so the
        # leader could not store its response either. A miss read just
        # before a leader stores may still lead a second render; that
        # window is one backend round trip.
        # A stale entry that only answers for a failing handler is waited
        # for like a miss: the leader's render may make it fresh.
        awaited = entry is None or (self.stale == "error" and self._is_stale(entry))
        if self.coalesce and read_ok and awaited:
            leader = call.flight.join(backend, cache_key, may_lead=req.method == "GET")
            if leader is not None:
                logger.debug("Waiting for a running miss; key=%s", cache_key)
                await _wait_for(leader)
                waited = True
            else:
                waited = await self._lock_or_wait(call, backend, cache_key)
            if waited:
                entry, _ = await _read_entry(
                    backend, cache_key, req, fail_open=self.fail_open
                )
                entry = self._usable(call, entry)
        return entry

    async def _lock_or_wait(
        self, call: _Call, backend: BaseCacheBackend, cache_key: str
    ) -> bool:
        """Take the key's lock across workers, or wait for the one who has it.

        Only with ``coalesce="distributed"``, and only for the request that
        leads in this process. A GET that gets the lock keeps it on ``call``
        while it renders. Otherwise another worker is rendering the key: this
        waits until its lock is gone, and returns True for the caller to read
        the backend again. Like a follower in one process, a request waits
        once and never takes the lock afterwards, so requests do not queue
        behind a response that is not stored. A HEAD never takes the lock.
        The wait ends after ``_LOCK_WAIT_MAX`` seconds at the latest.

        The lock renews itself while it is held, so a slow handler keeps it,
        and a worker that died frees it within ``_LOCK_TTL`` seconds.
        """
        if self.coalesce != "distributed":
            return False
        lock = _miss_lock(backend, cache_key)
        try:
            if call.request.method == "GET" and await lock.acquire(blocking=False):
                call.lock = lock
                return False
            # A GET that did not get the lock reads again even if the lock is
            # gone by the first look: its holder has stored in between.
            waited = call.request.method == "GET"
            delay = _LOCK_POLL_MIN
            deadline = time.monotonic() + _LOCK_WAIT_MAX
            while await lock.locked():
                if time.monotonic() >= deadline:
                    logger.warning(
                        "Another worker still renders after %ss; rendering "
                        "too; path=%s",
                        _LOCK_WAIT_MAX,
                        call.request.url.path,
                    )
                    break
                waited = True
                await asyncio.sleep(delay)
                delay = min(delay * 2, _LOCK_POLL_MAX)
        except Exception as e:
            if not self.fail_open:
                raise
            _log_backend_failure(
                "lock failed; rendering without it", call.request, cache_key, e
            )
            return False
        if waited:
            logger.debug("Waited for another worker's miss; key=%s", cache_key)
        return waited

    def _hit(
        self,
        call: _Call,
        entry: CacheEntry,
        cache_key: str,
        cache_control: str,
        *,
        outcome: str = _HIT,
    ) -> Response:
        """Serve the stored entry: a 304 for its ETag, or the entry itself."""
        call.outcome = outcome
        if _etag_matches(call.client_etag, entry.fingerprint):
            logger.debug("304 Not Modified (cached ETag match); key=%s", cache_key)
            # Answered from the stored entry, so the 304 says how old
            # that entry is: a cache refreshing its copy with this 304
            # takes the new Age with it (RFC 9111 §4.3.4).
            return _not_modified(
                entry.fingerprint,
                cache_control,
                entry.headers,
                _age_headers(entry, self.stored_ttl),
            )

        logger.debug("Cache HIT (TTL valid); key=%s", cache_key)
        hit = Response(
            content=entry.content,
            status_code=entry.status_code,
            media_type=entry.media_type,
        )
        # Framing and validator headers come from this response, not
        # the entry: an entry built outside @cache may still carry them.
        _append_headers(
            hit,
            (
                (name, value)
                for name, value in entry.headers
                if name.lower() not in _UNCACHEABLE_HEADERS
            ),
        )
        hit.headers["ETag"] = entry.fingerprint
        hit.headers["Cache-Control"] = cache_control
        hit.headers.update(_age_headers(entry, self.stored_ttl))
        return hit

    def _skip_reason(self, call: _Call, response: Response) -> CacheReason | None:
        """Why a rendered response is served but not stored, if it is not.

        A response scoped to one caller is never stored. An entry already
        under its key is left alone, as for an error status: it came from a
        response that was shareable.
        """
        reason = _unshareable_reason(response)
        if reason is None:
            reason = _dependency_unshareable_reason(call.dependency_lines)
        if reason is None and call.dependency_status is not None:
            # It may hold for this request only, and a hit would replay it.
            reason = "dependency-status"
        if reason is None and call.request.method == "HEAD":
            # A handler may skip the body for HEAD; a GET hit would then
            # replay the empty one.
            reason = "head"
        return reason

    async def _store(
        self,
        call: _Call,
        backend: BaseCacheBackend,
        cache_key: str,
        *,
        stored: CacheEntry | None,
        response: Response,
        etag: str,
        body: bytes,
    ) -> None:
        """Store a rendered response, unless it must not be or already is."""
        skip_reason = self._skip_reason(call, response)
        if skip_reason is not None:
            logger.debug("Not storing key=%s: %s", cache_key, skip_reason)
            call.reason = skip_reason
            return
        if call.tags_before is None:
            # Without the tag versions read before the handler ran, the entry
            # could never be invalidated: nothing is stored.
            call.reason = "tag-read-failed"
            return
        if (
            stored is not None
            and stored.fingerprint == etag
            and not self._is_stale(stored)
        ):
            return
        entry = _entry_for(response, etag, body)
        try:
            versions = await _tags.versions_to_record(backend, call.tags_before)
            if versions is None:
                logger.debug("A tag got its version meanwhile; key=%s", cache_key)
                call.reason = "tag-invalidated"
                return
            entry.tags = versions
            await backend.set(cache_key, entry, ttl=self.stored_ttl)
        except Exception as e:
            if not self.fail_open:
                _emit("error", "http", cache_key, route=_route_template(call.request))
                raise
            call.reason = "write-failed"
            _log_backend_failure(
                "write failed; response not stored", call.request, cache_key, e
            )
        else:
            logger.debug(
                "Updated cache entry; key=%s ttl=%s", cache_key, self.stored_ttl
            )
            _emit("store", "http", cache_key, route=_route_template(call.request))


def _miss_lock(backend: BaseCacheBackend, cache_key: str) -> CacheLock:
    """The lock of ``coalesce="distributed"`` for one key."""
    return CacheLock(cache_key, ttl=_LOCK_TTL, backend=backend, auto_extend=True)


# Releases on their way to the backend, so that they are not collected.
_pending_releases: set["asyncio.Future[bool]"] = set()


async def _release_lock(lock: CacheLock) -> None:
    """Release a ``coalesce="distributed"`` lock; a failure is only logged.

    The response is already made, and the lock expires on its own. The
    release runs as a task of its own, shielded: a request cancelled by a
    timeout is cancelled again at every await, and a lock left behind would
    make each miss of its key wait until it expired.
    """
    release = asyncio.ensure_future(lock.release())
    _pending_releases.add(release)
    release.add_done_callback(_pending_releases.discard)
    try:
        await asyncio.shield(release)
    except Exception as e:  # noqa: BLE001 - there is nothing left to fail
        logger.warning(
            "Releasing the miss lock failed (%s: %s); it expires in %ss",
            type(e).__name__,
            e,
            _LOCK_TTL,
        )


def _add_debug_headers(response: Response, call: _Call) -> None:
    """``X-Cache`` and ``X-Cache-Reason`` for a GET or HEAD response.

    Set after the entry was built, so they are never stored. They replace
    what the handler, a dependency or a stored entry put under these names.
    """
    if call.outcome is None:
        return
    response.headers[_DEBUG_HEADER] = call.outcome
    if call.reason is None:
        del response.headers[_REASON_HEADER]
    else:
        response.headers[_REASON_HEADER] = call.reason
