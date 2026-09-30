"""Session middleware for FastAPI."""

import logging
import warnings
from typing import TYPE_CHECKING
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import Session as StarletteSession
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from fastapi_cachex.headers import add_vary

from .config import SessionConfig
from .exceptions import SessionError
from .manager import SessionManager
from .proxy import SessionManagerProxy

if TYPE_CHECKING:
    from collections.abc import Collection

    from .models import Session
    from .models import SessionUser

logger = logging.getLogger(__name__)


def get_client_ip(connection: HTTPConnection, config: SessionConfig) -> str | None:
    """Get the client IP address from an HTTP connection.

    This is the address the session middleware checks `ip_binding` against.
    Pass the same value to `SessionManager.create_session()` so the address
    stored at login matches the one checked on later requests, which
    `request.client.host` does not when the app runs behind a trusted proxy.

    `X-Forwarded-For` and `X-Real-IP` are believed only when the request
    actually arrived from one of `config.trusted_proxies`; with the default
    empty list they are ignored entirely, because anyone can send them.

    Even behind a trusted proxy the leftmost `X-Forwarded-For` entry is not the
    client: proxies append, so a caller who sends the header themselves has
    their value sitting in front of the address the proxy added. This walks the
    chain from the right and takes the first address that is not a trusted
    proxy — the closest hop nobody in the chain could have forged. If every
    entry is a trusted proxy there is no client address to recover and the
    direct peer is used.

    Args:
        connection: Incoming HTTP connection (or a `Request`, which IS-A
            `HTTPConnection`)
        config: Session configuration carrying the trusted proxy list

    Returns:
        Client IP address or None
    """
    peer = connection.client.host if connection.client else None

    if peer is not None and config.is_trusted_proxy(peer):
        # A proxy may add its own header line instead of appending to the
        # caller's, so the chain is every line joined, not just the first one.
        forwarded_for = ",".join(connection.headers.getlist("x-forwarded-for"))
        if forwarded_for:
            for entry in reversed(forwarded_for.split(",")):
                candidate = entry.strip()
                if candidate and not config.is_trusted_proxy(candidate):
                    logger.debug("Client IP from X-Forwarded-For: %s", candidate)
                    return candidate

        # X-Real-IP is written by the proxy itself, so it has no chain to walk.
        real_ip = connection.headers.get("x-real-ip")
        if real_ip:
            logger.debug("Client IP from X-Real-IP: %s", real_ip)
            return real_ip

    if peer is not None:
        logger.debug("Client IP from connection: %s", peer)

    return peer


def _read_header_token(
    connection: HTTPConnection, config: SessionConfig
) -> tuple[str | None, list[str]]:
    """Extract a session token and name the request headers that were read.

    The response depends on every header read before the token was found, so
    those are the names it must ``Vary`` on.

    Args:
        connection: Incoming HTTP connection
        config: Session configuration

    Returns:
        ``(token, consulted)``: the token or None, and the header names read,
        in the order they were checked
    """
    consulted: list[str] = []
    # `token_source_priority` is a list of Literals, so pydantic has already
    # rejected anything else; the chain stays an `elif` so "cookie" (read by
    # FastAPICacheXSessionMiddleware after these) and any source added later
    # fall through instead of being read as a bearer token.
    for source in config.token_source_priority:
        if source == "header":
            consulted.append(config.header_name)
            token = connection.headers.get(config.header_name)
            if token:
                logger.debug("Token extracted from header")
                return token, consulted

        elif source == "bearer":
            if config.use_bearer_token:
                consulted.append("Authorization")
                # The scheme name is case-insensitive (RFC 9110 §11.1) and is
                # followed by one or more spaces (RFC 6750 §2.1).
                scheme, _, token_value = connection.headers.get(
                    "authorization", ""
                ).partition(" ")
                token_value = token_value.lstrip(" ")
                if scheme.lower() == "bearer" and token_value:
                    logger.debug("Token extracted from bearer auth")
                    return token_value, consulted

    return None, consulted


# Set on the request state when a session dependency reads the loaded session,
# so the middleware knows the response depends on the token sources (#372).
_SESSION_READ_KEY = "__fastapi_cachex_session_read"


def _session_was_read(connection: HTTPConnection) -> bool:
    """Whether a session dependency read the session for this request."""
    return bool(connection.scope.get("state", {}).get(_SESSION_READ_KEY))


def _forbid_storing(headers: MutableHeaders) -> None:
    """Keep a response that carries a session token out of every cache.

    The token is a credential: a shared cache that stored the response would
    hand it to the next visitor. This replaces any ``Cache-Control`` the route
    set, ``public`` and ``max-age`` included.

    Args:
        headers: Mutable response headers to write to
    """
    headers["Cache-Control"] = "private, no-store"


def _stash_session_manager(app: Any, manager: SessionManager) -> None:
    """Register the session manager on ``app.state`` for dependency injection.

    Stored under a private key on the first request only; ``get_session_manager``
    reads it back.

    Args:
        app: The Starlette application (``scope["app"]`` / ``request.app``)
        manager: Session manager to register
    """
    if not hasattr(app.state, "__fastapi_cachex_session_manager"):
        setattr(app.state, "__fastapi_cachex_session_manager", manager)


def _warn_if_priority_without_cookie(config: SessionConfig) -> None:
    """Warn that 0.4.0 stops using the cookie for a list without ``"cookie"``.

    In 0.4.0 ``token_source_priority`` lists every token source, so a list
    set without ``"cookie"`` means the middleware neither reads nor sets the
    session cookie (#75). The default list becomes ``["header", "bearer",
    "cookie"]``, which is today's order, so it does not warn.

    Args:
        config: The configuration the middleware uses
    """
    if (
        "token_source_priority" not in config.model_fields_set
        or "cookie" in config.token_source_priority
    ):
        return
    warnings.warn(
        f"SessionConfig(token_source_priority={config.token_source_priority!r}) "
        'does not list "cookie". In version 0.4.0 the list names every token '
        "source, so FastAPICacheXSessionMiddleware will neither read nor set the "
        "session cookie, and a session created for a request without a token "
        'sends its token in the header_name response header. Add "cookie" as the '
        "last entry to keep the cookie; leaving it out opts into 0.4.0's "
        "cookie-less behaviour, which only takes effect then "
        "(https://github.com/allen0099/FastAPI-CacheX/issues/75).",
        FutureWarning,
        stacklevel=3,
    )


class _RequestSession(StarletteSession):
    """``request.session`` that remembers an explicit ``clear()``.

    ``clear()`` is the logout idiom, so it has to end the loaded session even
    when its data was already empty, while removing the last key with
    ``del``/``pop`` must not log a user out.
    """

    cleared: bool = False
    # The backend session this dict belongs to: the one the middleware loaded,
    # or the one ``login()`` started or rotated. Read when the response starts.
    backend: "Session | None" = None
    # The middleware that created this dict; ``login()`` goes through it.
    middleware: "FastAPICacheXSessionMiddleware | None" = None

    def clear(self) -> None:
        self.cleared = True
        super().clear()


class FastAPICacheXSessionMiddleware:
    """Drop-in-compatible replacement for Starlette's ``SessionMiddleware``.

    Provides the same ``request.session`` / ``scope["session"]`` dict-like
    interface as ``starlette.middleware.sessions.SessionMiddleware``, but the
    session payload is persisted via the configured ``SessionManager``/cache
    backend instead of being encoded into the cookie itself. Only a signed
    session token is stored client-side, in the cookie named by
    ``SessionConfig.cookie_name``. A token in the custom header or an
    ``Authorization: Bearer`` header is read before the cookie, and a request
    that sent one (even one that no longer resolves) gets its token back in
    the ``header_name`` response header instead of ``Set-Cookie``.
    """

    def __init__(
        self,
        app: ASGIApp,
        session_manager: SessionManager | None = None,
        config: SessionConfig | None = None,
    ) -> None:
        """Initialize the Starlette-aligned session middleware.

        Args:
            app: ASGI application
            session_manager: Session manager instance; defaults to the one
                set in ``SessionManagerProxy``
            config: Session configuration; defaults to
                ``session_manager.config``

        Raises:
            ProxyNotSetError: If ``session_manager`` is omitted and
                ``SessionManagerProxy`` holds none.

        Warns:
            FutureWarning: If the configuration sets ``token_source_priority``
                without ``"cookie"``, which disables the cookie in 0.4.0.
        """
        self.app = app
        self.session_manager = session_manager or SessionManagerProxy.get()
        self.config = config or self.session_manager.config
        _warn_if_priority_without_cookie(self.config)

        security_flags = f"httponly; samesite={self.config.cookie_same_site}"
        if self.config.cookie_https_only:
            security_flags += "; secure"
        if self.config.cookie_domain is not None:
            security_flags += f"; domain={self.config.cookie_domain}"
        self._security_flags = security_flags

        logger.debug(
            "FastAPICacheXSessionMiddleware initialized; cookie=%s path=%s",
            self.config.cookie_name,
            self.config.cookie_path,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Load the session for the connection and persist it on response.

        Args:
            scope: ASGI connection scope
            receive: ASGI receive callable
            send: ASGI send callable
        """
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Store session manager in app state for dependency injection (first request only)
        _stash_session_manager(scope["app"], self.session_manager)

        connection = HTTPConnection(scope)
        loaded_token: str | None = None
        renewed_token: str | None = None
        backend_session: Session | None = None
        loaded_session_id: str | None = None

        # Resolve the incoming session token: prefer the header/bearer transport
        # (e.g. X-Session-Token) and fall back to the session cookie, so
        # header-based clients authenticate here too.
        # `header_token` is captured so the response is routed by transport: a
        # header-sourced token is echoed back via the response header, otherwise
        # via Set-Cookie (see send_wrapper).
        header_token, vary_on = self._token_sources(connection)
        token_value = header_token or connection.cookies.get(self.config.cookie_name)
        if token_value:
            try:
                ip_address = get_client_ip(connection, self.config)
                user_agent = connection.headers.get("user-agent")
                backend_session, renewed_token = await self.session_manager.get_session(
                    token_value,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                loaded_token = renewed_token or token_value
                loaded_session_id = backend_session.session_id
            except SessionError:
                logger.debug(
                    "FastAPICacheXSessionMiddleware: token invalid/expired; "
                    "starting empty session",
                )

        request_session = _RequestSession(
            backend_session.data if backend_session is not None else {}
        )
        request_session.backend = backend_session
        request_session.middleware = self
        scope["session"] = request_session

        scope.setdefault("state", {})["__fastapi_cachex_session"] = backend_session

        # Route the response by how the token arrived: header-sourced tokens are
        # echoed back via the response header (no cookies); cookie-sourced (or
        # brand-new) sessions use Set-Cookie.
        from_header = header_token is not None

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                session: _RequestSession = scope["session"]
                headers = MutableHeaders(scope=message)

                # login() may have started a session, or replaced the loaded one.
                target = request_session.backend
                current_token, fresh_token = self._response_tokens(
                    target, loaded_session_id, loaded_token, renewed_token
                )

                sent_token = await self._persist(
                    session,
                    headers,
                    connection,
                    target,
                    current_token,
                    fresh_token,
                    from_header=from_header,
                )

                if session.accessed or sent_token or _session_was_read(connection):
                    add_vary(headers, vary_on)
                if sent_token:
                    _forbid_storing(headers)

            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _persist(  # noqa: PLR0913, PLR0917
        self,
        session: _RequestSession,
        headers: MutableHeaders,
        connection: HTTPConnection,
        backend_session: "Session | None",
        current_token: str | None,
        fresh_token: str | None,
        *,
        from_header: bool,
    ) -> bool:
        """Save, delete or renew the session according to what the request did to it.

        Returns:
            True if a token or a clearing cookie was written to the response
        """
        sent_token = False
        target = backend_session
        if session.cleared and target is not None:
            # clear() logs out, whatever the data held. Anything
            # written after it goes into a new anonymous session.
            await self.session_manager.delete_session(target.session_id)
            target = current_token = fresh_token = None
            if not session and not from_header:
                # Cookie transport: expire the cookie. A header-based
                # client simply drops its now-dangling token.
                headers.append("Set-Cookie", self._build_clear_cookie_header())
                sent_token = True

        if session.modified and (
            session or (target is not None and target.user is not None)
        ):
            # A logged-in session emptied with del/pop keeps its user
            # and is saved with empty data.
            cookie_token, new_token = await self._write_session(
                session,
                connection,
                target,
                current_token,
                fresh_token,
            )
            # Header clients only need a genuinely new/renewed token (an
            # unchanged one is already held); cookie clients always get a
            # refreshed cookie.
            token_to_emit = new_token if from_header else cookie_token
            if token_to_emit is not None:
                self._emit_token(headers, token_to_emit, from_header=from_header)
                sent_token = True
        elif session.modified and target is not None:
            # An anonymous session left empty holds nothing to keep.
            await self.session_manager.delete_session(target.session_id)
            if not from_header:
                headers.append("Set-Cookie", self._build_clear_cookie_header())
                sent_token = True
        elif fresh_token is not None:
            # Sliding expiration renewed the token, or the ID was
            # regenerated, even though the dict itself was untouched;
            # propagate it via the same transport.
            self._emit_token(headers, fresh_token, from_header=from_header)
            sent_token = True
        return sent_token

    def _token_sources(
        self, connection: HTTPConnection
    ) -> tuple[str | None, list[str]]:
        """The header-carried token, if any, and the request headers to Vary on.

        The response depends on every header read to find the token. The
        cookie is read only when no header carried one.
        """
        header_token, vary_on = _read_header_token(connection, self.config)
        if header_token is None:
            vary_on.append("Cookie")
        return header_token, vary_on

    def _response_tokens(
        self,
        backend_session: "Session | None",
        loaded_session_id: str | None,
        loaded_token: str | None,
        renewed_token: str | None,
    ) -> tuple[str | None, str | None]:
        """The ``(current, fresh)`` tokens to answer with.

        Normally the loaded token and the sliding-renewed one (if any). If the
        handler regenerated the session ID (at login, say), the loaded token
        names a deleted record, so both become a token for the new ID and the
        client gets it on either transport.
        """
        if backend_session is None or backend_session.session_id == loaded_session_id:
            return loaded_token, renewed_token
        token = self.session_manager.issue_token(backend_session)
        return token, token

    def _emit_token(
        self, headers: MutableHeaders, token: str, *, from_header: bool
    ) -> None:
        """Send a session token to the client via its transport.

        Args:
            headers: Mutable response headers to write to
            token: Session token string to deliver
            from_header: If True, echo via the configured response header;
                otherwise (re)set it as a Set-Cookie header.
        """
        if from_header:
            headers.append(self.config.header_name, token)
        else:
            headers.append("Set-Cookie", self._build_set_cookie_header(token))

    async def _write_session(
        self,
        session: StarletteSession,
        connection: HTTPConnection,
        backend_session: "Session | None",
        loaded_token: str | None,
        renewed_token: str | None,
    ) -> tuple[str, str | None]:
        """Create-or-update the backend session for a modified dict.

        The dict is non-empty, or was emptied on a session that has a user.

        Args:
            session: The Starlette session dict for this request
            connection: Incoming HTTP connection (for IP / User-Agent binding)
            backend_session: The session the dict belongs to (loaded, or
                started by ``login()``), or None to create an anonymous one
            loaded_token: Token for the loaded session (None when creating anew)
            renewed_token: Sliding-expiration renewed token, if any

        Returns:
            ``(cookie_token, new_token)`` where ``cookie_token`` is the token to
            (re)set as a cookie, and ``new_token`` is a genuinely new/renewed token
            to hand a header-based client (``None`` when the token is unchanged).
        """
        if backend_session is None:
            (
                backend_session,
                loaded_token,
            ) = await self.session_manager.create_anonymous_session(
                ip_address=get_client_ip(connection, self.config),
                user_agent=connection.headers.get("user-agent"),
            )
            new_token: str | None = loaded_token
        else:
            # backend_session and loaded_token are always set together (after a
            # successful get_session() call).
            assert loaded_token is not None  # noqa: S101
            new_token = renewed_token
        backend_session.data = dict(session)
        await self.session_manager.update_session(backend_session)
        return loaded_token, new_token

    def _build_set_cookie_header(self, token: str) -> str:
        """Build a `Set-Cookie` header value carrying the session token.

        Args:
            token: Signed session token string

        Returns:
            Set-Cookie header value
        """
        max_age = (
            f"Max-Age={self.config.cookie_max_age}; "
            if self.config.cookie_max_age
            else ""
        )
        return (
            f"{self.config.cookie_name}={token}; "
            f"path={self.config.cookie_path}; "
            f"{max_age}"
            f"{self._security_flags}"
        )

    def _build_clear_cookie_header(self) -> str:
        """Build a `Set-Cookie` header value that expires the session cookie.

        Returns:
            Set-Cookie header value
        """
        return (
            f"{self.config.cookie_name}=; "
            f"path={self.config.cookie_path}; "
            f"expires=Thu, 01 Jan 1970 00:00:00 GMT; "
            f"{self._security_flags}"
        )


async def _log_in(
    connection: HTTPConnection,
    request_session: _RequestSession,
    user: "SessionUser",
    keep: "Collection[str] | None" = None,
) -> "Session":
    """Attach ``user`` to the request's session under a new session ID.

    The public entry point is ``fastapi_cachex.session.login()``, which
    documents the behaviour and checks that ``request_session`` belongs to
    ``FastAPICacheXSessionMiddleware``.

    Args:
        connection: The request being handled
        request_session: The middleware's ``request.session`` for it
        user: The user to attach
        keep: The ``request.session`` keys to carry into the logged-in
            session, or None for all of them

    Returns:
        The logged-in session
    """
    middleware = request_session.middleware
    assert middleware is not None  # noqa: S101 - checked by login()
    manager = middleware.session_manager
    current = request_session.backend
    if request_session.cleared:
        # clear() already logged the loaded session out. Delete it now, since
        # the flag that would have deleted it is reset below, and log in on a
        # new session.
        if current is not None:
            await manager.delete_session(current.session_id)
        current = None
        request_session.cleared = False
    elif (
        current is not None
        and current.user is not None
        and current.user.user_id != user.user_id
    ):
        # Another user's session: nothing in it belongs to the new user. Delete
        # it and empty the request dict (without marking a logout), so the new
        # session starts with only what the handler writes after login().
        await manager.delete_session(current.session_id)
        current = None
        dict.clear(request_session)

    if keep is not None:
        # pop() marks the dict modified, so the middleware saves what is left.
        for key in [key for key in request_session if key not in keep]:
            request_session.pop(key)
        if current is not None:
            # Rotation stores ``current`` under the new ID: keep the dropped
            # data out of that record too.
            current.data = {k: v for k, v in current.data.items() if k in keep}

    if current is None:
        current, _ = await manager.create_session(
            user,
            ip_address=get_client_ip(connection, middleware.config),
            user_agent=connection.headers.get("user-agent"),
        )
    else:
        # Attach the user before the rotation, so the record under the old ID
        # never holds it.
        current._attach_user(user)  # noqa: SLF001
        await manager.regenerate_session_id(current)

    # The session is already stored with the user. Its ID differs from the one
    # the middleware loaded (if any), so the middleware sends a token for it
    # and saves ``request.session`` into it if the handler writes there.
    request_session.backend = current
    connection.scope.setdefault("state", {})["__fastapi_cachex_session"] = current
    return current
