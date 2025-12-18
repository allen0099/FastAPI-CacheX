"""Session middleware for FastAPI."""

from typing import TYPE_CHECKING

from fastapi import Request
from fastapi import Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import ASGIApp

from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.exceptions import SessionError
from fastapi_cachex.session.manager import SessionManager

if TYPE_CHECKING:
    from fastapi_cachex.session.models import Session


class SessionMiddleware(BaseHTTPMiddleware):
    """Middleware to handle session loading and cookie management."""

    def __init__(
        self,
        app: ASGIApp,
        session_manager: SessionManager,
        config: SessionConfig,
    ) -> None:
        """Initialize session middleware.

        Args:
            app: ASGI application
            session_manager: Session manager instance
            config: Session configuration
        """
        super().__init__(app)
        self.session_manager = session_manager
        self.config = config

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
            except SessionError:
                # Session invalid/expired, continue without session
                session = None

        # Store session in request state
        request.state.session = session

        # Process request
        response: Response = await call_next(request)

        # Update session cookie if session exists and was modified
        if (
            session
            and token
            and hasattr(request.state, "session_modified")
            and request.state.session_modified
        ):
            await self.session_manager.update_session(session)
            self._set_session_cookie(response, token)

        # Set new session cookie if session was just created
        if hasattr(request.state, "new_session_token"):
            self._set_session_cookie(response, request.state.new_session_token)

        return response

    def _extract_token(self, request: Request) -> str | None:
        """Extract session token from request.

        Args:
            request: Incoming request

        Returns:
            Session token or None
        """
        for source in self.config.token_source_priority:
            if source == "cookie":
                token = request.cookies.get(self.config.cookie_name)
                if token:
                    return token

            elif source == "header":
                token = request.headers.get(self.config.header_name)
                if token:
                    return token

            elif source == "bearer":
                if self.config.use_bearer_token:
                    auth_header = request.headers.get("authorization")
                    if auth_header and auth_header.startswith("Bearer "):
                        bearer_prefix_len = 7
                        return auth_header[bearer_prefix_len:]

        return None

    def _set_session_cookie(self, response: Response, token: str) -> None:
        """Set session cookie in response.

        Args:
            response: Response object
            token: Session token
        """
        response.set_cookie(
            key=self.config.cookie_name,
            value=token,
            max_age=self.config.cookie_max_age,
            path=self.config.cookie_path,
            domain=self.config.cookie_domain,
            secure=self.config.cookie_secure,
            httponly=self.config.cookie_httponly,
            samesite=self.config.cookie_samesite,
        )

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
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        # Fallback to direct client IP
        if request.client:
            return request.client.host

        return None
