"""Session middleware for FastAPI."""

import logging
import warnings
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi import Response
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from .config import SessionConfig
from .exceptions import SessionError
from .manager import SessionManager
from .proxy import SessionManagerProxy

if TYPE_CHECKING:
    # starlette.middleware.sessions unconditionally imports itsdangerous, an
    # optional dependency (fastapi-cachex[starlette]) only needed at runtime by
    # StarletteSessionMiddleware; import it lazily there instead (see its
    # __init__) so the rest of this module doesn't require it.
    from starlette.middleware.sessions import Session as StarletteSession

    from .models import Session

logger = logging.getLogger(__name__)


def _get_client_ip(connection: HTTPConnection) -> str | None:
    """Get client IP address from an HTTP connection.

    Args:
        connection: Incoming HTTP connection (or a `Request`, which IS-A
            `HTTPConnection`)

    Returns:
        Client IP address or None
    """
    # Check X-Forwarded-For header (for proxied requests)
    forwarded_for = connection.headers.get("x-forwarded-for")
    if forwarded_for:
        # Get first IP from comma-separated list
        ip = forwarded_for.split(",")[0].strip()
        logger.debug("Client IP from X-Forwarded-For: %s", ip)
        return ip

    # Check X-Real-IP header
    real_ip = connection.headers.get("x-real-ip")
    if real_ip:
        logger.debug("Client IP from X-Real-IP: %s", real_ip)
        return real_ip

    # Fallback to direct client IP
    if connection.client:
        logger.debug("Client IP from connection: %s", connection.client.host)
        return connection.client.host

    return None


class SessionMiddleware(BaseHTTPMiddleware):
    """Middleware to handle session loading and token extraction.

    Extracts the session token from the request (via a custom header and/or
    an ``Authorization: Bearer`` header, per ``SessionConfig.token_source_priority``)
    and loads the corresponding session into ``request.state``. Cookie-based
    token transport is not currently supported.

    .. deprecated:: 0.3.1
        Use :class:`StarletteSessionMiddleware` instead. Will be removed in
        version 0.3.5.
    """

    def __init__(
        self,
        app: ASGIApp,
        session_manager: SessionManager | None = None,
        config: SessionConfig | None = None,
    ) -> None:
        """Initialize session middleware.

        Args:
            app: ASGI application
            session_manager: Session manager instance
            config: Session configuration
        """
        warnings.warn(
            "SessionMiddleware is deprecated, use StarletteSessionMiddleware. "
            "Will be removed in version 0.3.5.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(app)
        self.session_manager = session_manager or SessionManagerProxy.get()

        if config is None:
            config = self.session_manager.config

        self.config = config

        logger.debug(
            "SessionMiddleware initialized; header=%s bearer=%s",
            config.header_name,
            config.use_bearer_token,
        )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process request and handle session.

        Args:
            request: Incoming request
            call_next: Next handler in chain

        Returns:
            Response
        """
        # Store session manager in app state for dependency injection (first request only)
        # This allows developers to use get_session_manager() dependency
        if not hasattr(request.app.state, "__fastapi_cachex_session_manager"):
            setattr(
                request.app.state,
                "__fastapi_cachex_session_manager",
                self.session_manager,
            )

        # Extract session token from request
        token = self._extract_token(request)

        # Try to load session
        session: Session | None = None
        renewed_token: str | None = None
        if token:
            try:
                ip_address = self._get_client_ip(request)
                user_agent = request.headers.get("user-agent")
                session, renewed_token = await self.session_manager.get_session(
                    token,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                logger.debug("Session loaded in middleware; id=%s", session.session_id)
            except SessionError:
                # Session invalid/expired, continue without session
                session = None
                logger.debug("Session failed to load; token invalid/expired")

        # Store session in request state
        setattr(request.state, "__fastapi_cachex_session", session)

        # Process request
        response: Response = await call_next(request)

        # Propagate renewed token to client so its JWT exp stays in sync
        if renewed_token is not None:
            response.headers[self.config.header_name] = renewed_token

        return response

    def _extract_token(self, request: Request) -> str | None:
        """Extract session token from request.

        Args:
            request: Incoming request

        Returns:
            Session token or None
        """
        for source in self.config.token_source_priority:
            if source == "header":
                token = request.headers.get(self.config.header_name)
                if token:
                    logger.debug("Token extracted from header")
                    return token

            elif source == "bearer":
                if self.config.use_bearer_token:
                    auth_header = request.headers.get("authorization")
                    if auth_header and auth_header.startswith("Bearer "):
                        bearer_prefix_len = 7
                        token_value = auth_header[bearer_prefix_len:]
                        logger.debug("Token extracted from bearer auth")
                        return token_value

        return None

    def _get_client_ip(self, request: Request) -> str | None:
        """Get client IP address from request.

        Args:
            request: Incoming request

        Returns:
            Client IP address or None
        """
        return _get_client_ip(request)


class StarletteSessionMiddleware:
    """Drop-in-compatible replacement for Starlette's ``SessionMiddleware``.

    Provides the same ``request.session`` / ``scope["session"]`` dict-like
    interface as ``starlette.middleware.sessions.SessionMiddleware``, but the
    session payload is persisted via the configured ``SessionManager``/cache
    backend instead of being encoded into the cookie itself. Only a signed
    session token is stored client-side, in the cookie named by
    ``SessionConfig.cookie_name``.
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
            session_manager: Session manager instance
            config: Session configuration
        """
        try:
            from starlette.middleware.sessions import Session as _StarletteSession
        except ImportError as e:  # pragma: no cover
            msg = (
                "StarletteSessionMiddleware requires itsdangerous; "
                "install fastapi-cachex[starlette]"
            )
            raise ImportError(msg) from e
        self._session_cls: type[StarletteSession] = _StarletteSession

        self.app = app
        self.session_manager = session_manager or SessionManagerProxy.get()
        self.config = config or self.session_manager.config

        security_flags = f"httponly; samesite={self.config.cookie_same_site}"
        if self.config.cookie_https_only:
            security_flags += "; secure"
        if self.config.cookie_domain is not None:
            security_flags += f"; domain={self.config.cookie_domain}"
        self._security_flags = security_flags

        logger.debug(
            "StarletteSessionMiddleware initialized; cookie=%s path=%s",
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
        app = scope["app"]
        if not hasattr(app.state, "__fastapi_cachex_session_manager"):
            setattr(
                app.state,
                "__fastapi_cachex_session_manager",
                self.session_manager,
            )

        connection = HTTPConnection(scope)
        initial_session_was_empty = True
        loaded_token: str | None = None
        renewed_token: str | None = None
        backend_session: Session | None = None

        cookie_value = connection.cookies.get(self.config.cookie_name)
        if cookie_value:
            try:
                ip_address = _get_client_ip(connection)
                user_agent = connection.headers.get("user-agent")
                backend_session, renewed_token = await self.session_manager.get_session(
                    cookie_value,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                loaded_token = renewed_token or cookie_value
                scope["session"] = self._session_cls(backend_session.data)
                initial_session_was_empty = not backend_session.data
            except SessionError:
                logger.debug(
                    "StarletteSessionMiddleware: token invalid/expired; "
                    "starting empty session",
                )
                scope["session"] = self._session_cls()
        else:
            scope["session"] = self._session_cls()

        scope.setdefault("state", {})["__fastapi_cachex_session"] = backend_session

        async def send_wrapper(message: Message) -> None:
            nonlocal backend_session, loaded_token

            if message["type"] == "http.response.start":
                session: StarletteSession = scope["session"]
                headers = MutableHeaders(scope=message)

                if session.accessed:
                    headers.add_vary_header("Cookie")

                if session.modified and session:
                    # Dict has content -> persist (create or update) and set cookie.
                    if backend_session is None:
                        (
                            backend_session,
                            loaded_token,
                        ) = await self.session_manager.create_anonymous_session(
                            ip_address=_get_client_ip(connection),
                            user_agent=connection.headers.get("user-agent"),
                        )
                    else:
                        # backend_session and loaded_token are always set together
                        # (either just above, or after a successful get_session()
                        # call before this closure was defined).
                        assert loaded_token is not None  # noqa: S101
                    backend_session.data = dict(session)
                    await self.session_manager.update_session(backend_session)

                    headers.append(
                        "Set-Cookie", self._build_set_cookie_header(loaded_token)
                    )
                elif session.modified and not initial_session_was_empty:
                    # Cleared -> delete backend session, expire cookie.
                    # backend_session is always set when initial_session_was_empty
                    # is False (both are only set together, after a successful
                    # get_session() call above).
                    assert backend_session is not None  # noqa: S101
                    await self.session_manager.delete_session(
                        backend_session.session_id
                    )
                    headers.append("Set-Cookie", self._build_clear_cookie_header())
                elif renewed_token is not None:
                    # Sliding expiration renewed the token even though the dict
                    # itself was untouched; the cookie must still be refreshed.
                    headers.append(
                        "Set-Cookie", self._build_set_cookie_header(renewed_token)
                    )

            await send(message)

        await self.app(scope, receive, send_wrapper)

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
