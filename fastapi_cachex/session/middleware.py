"""Session middleware for FastAPI."""

import logging
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi import Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import ASGIApp

from .config import SessionConfig
from .exceptions import SessionError
from .manager import SessionManager
from .proxy import SessionManagerProxy

if TYPE_CHECKING:
    from .models import Session

logger = logging.getLogger(__name__)


class SessionMiddleware(BaseHTTPMiddleware):
    """Middleware to handle session loading and cookie management."""

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
        if token:
            try:
                ip_address = self._get_client_ip(request)
                user_agent = request.headers.get("user-agent")
                session = await self.session_manager.get_session(
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
        # Check X-Forwarded-For header (for proxied requests)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # Get first IP from comma-separated list
            ip = forwarded_for.split(",")[0].strip()
            logger.debug("Client IP from X-Forwarded-For: %s", ip)
            return ip

        # Check X-Real-IP header
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            logger.debug("Client IP from X-Real-IP: %s", real_ip)
            return real_ip

        # Fallback to direct client IP
        if request.client:
            logger.debug("Client IP from connection: %s", request.client.host)
            return request.client.host

        return None
