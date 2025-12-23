"""Session manager for CRUD operations."""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.types import ETagContent

from .config import SessionConfig
from .exceptions import SessionExpiredError
from .exceptions import SessionInvalidError
from .exceptions import SessionNotFoundError
from .exceptions import SessionSecurityError
from .exceptions import SessionTokenError
from .models import Session
from .models import SessionStatus
from .models import SessionToken
from .models import SessionUser
from .security import SecurityManager
from .token_serializers import JWTTokenSerializer
from .token_serializers import SimpleTokenSerializer
from .token_serializers import TokenSerializer

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages session lifecycle and storage."""

    def __init__(
        self,
        backend: BaseCacheBackend,
        config: SessionConfig,
        token_serializer: TokenSerializer | None = None,
    ) -> None:
        """Initialize session manager.

        Args:
            backend: Cache backend for session storage
            config: Session configuration
            token_serializer: Optional custom token serializer. If provided,
                overrides the built-in selection (simple/jwt).
        """
        self.backend = backend
        self.config = config
        secret_value = config.secret_key.get_secret_value()
        self.security = SecurityManager(secret_value)
        # Select token serializer strategy (allow DI override)
        if token_serializer is not None:
            self._serializer = token_serializer
        elif config.token_format == "jwt":
            self._serializer = JWTTokenSerializer(config)
        else:
            self._serializer = SimpleTokenSerializer()
        logger.debug("Token serializer: %s", type(self._serializer).__name__)
        logger.debug(
            "SessionManager initialized with backend prefix=%s",
            config.backend_key_prefix,
        )

    def _get_backend_key(self, session_id: str) -> str:
        """Get backend storage key for a session.

        Args:
            session_id: The session ID

        Returns:
            Backend storage key
        """
        return f"{self.config.backend_key_prefix}{session_id}"

    async def create_session(
        self,
        user: SessionUser,
        ip_address: str | None = None,
        user_agent: str | None = None,
        **extra_data: dict[str, object],
    ) -> tuple[Session, str]:
        """Create a new session for an authenticated user.

        Args:
            user: Authenticated user data
            ip_address: Client IP address (if IP binding enabled)
            user_agent: Client User-Agent (if UA binding enabled)
            **extra_data: Additional session data

        Returns:
            Tuple of (Session, token_string)
        """
        return await self._create_session(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            **extra_data,
        )

    async def create_anonymous_session(
        self,
        ip_address: str | None = None,
        user_agent: str | None = None,
        **extra_data: dict[str, object],
    ) -> tuple[Session, str]:
        """Create a new session without user information."""
        return await self._create_session(
            user=None,
            ip_address=ip_address,
            user_agent=user_agent,
            **extra_data,
        )

    async def _create_session(
        self,
        user: SessionUser | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        **extra_data: dict[str, object],
    ) -> tuple[Session, str]:
        """Internal helper to create and persist a session."""
        session = Session(
            user=user,
            data=extra_data,
        )

        # Set expiry
        if self.config.session_ttl:
            session.expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=self.config.session_ttl,
            )

        # Bind IP and User-Agent if configured
        if self.config.ip_binding and ip_address:
            session.ip_address = ip_address
        if self.config.user_agent_binding and user_agent:
            session.user_agent = user_agent

        # Store in backend
        await self._save_session(session)

        # Generate signed token
        token = self._create_token(session.session_id)
        logger.debug(
            "Session created; id=%s ttl=%s ip=%s ua=%s",
            session.session_id,
            self.config.session_ttl,
            session.ip_address,
            session.user_agent,
        )

        return session, self._serializer.to_string(token)

    async def get_session(
        self,
        token_string: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Session:
        """Retrieve and validate a session.

        Args:
            token_string: Session token string
            ip_address: Current request IP address
            user_agent: Current request User-Agent

        Returns:
            Session object

        Raises:
            SessionTokenError: If token is invalid
            SessionNotFoundError: If session not found
            SessionExpiredError: If session has expired
            SessionInvalidError: If session is not active
            SessionSecurityError: If security checks fail
        """
        # Parse and verify token
        try:
            token = self._serializer.from_string(token_string)
        except ValueError as e:
            logger.debug("Session token parse error: %s", e)
            raise SessionTokenError(str(e)) from e
        # For simple format, verify signature explicitly
        if self.config.token_format == "simple" and not self.security.verify_signature(
            token.session_id,
            token.signature,
        ):
            msg = "Invalid session signature"
            logger.debug(
                "Session signature verification failed; id=%s", token.session_id
            )
            raise SessionSecurityError(msg)

        # Load session from backend
        session = await self._load_session(token.session_id)
        if not session:
            msg = f"Session {token.session_id} not found"
            logger.debug("Session not found; id=%s", token.session_id)
            raise SessionNotFoundError(msg)

        # Validate session
        if session.status != SessionStatus.ACTIVE:
            msg = f"Session is {session.status}"
            logger.debug(
                "Session not active; id=%s status=%s",
                session.session_id,
                session.status,
            )
            raise SessionInvalidError(msg)

        if session.is_expired():
            session.status = SessionStatus.EXPIRED
            await self._save_session(session)
            msg = "Session has expired"
            logger.debug("Session expired; id=%s", session.session_id)
            raise SessionExpiredError(msg)

        # Security checks
        if self.config.ip_binding and not self.security.check_ip_match(
            session,
            ip_address,
        ):
            msg = "IP address mismatch"
            logger.debug(
                "IP mismatch; id=%s expected=%s got=%s",
                session.session_id,
                session.ip_address,
                ip_address,
            )
            raise SessionSecurityError(msg)

        if self.config.user_agent_binding and not self.security.check_user_agent_match(
            session,
            user_agent,
        ):
            msg = "User-Agent mismatch"
            logger.debug(
                "UA mismatch; id=%s expected=%s got=%s",
                session.session_id,
                session.user_agent,
                user_agent,
            )
            raise SessionSecurityError(msg)

        # Update last accessed and handle sliding expiration
        session.update_last_accessed()

        if self.config.sliding_expiration and session.expires_at:
            time_remaining = (
                session.expires_at - datetime.now(timezone.utc)
            ).total_seconds()
            threshold = self.config.session_ttl * self.config.sliding_threshold

            if time_remaining < threshold:
                session.renew(self.config.session_ttl)
                logger.debug(
                    "Session renewed (sliding expiration); id=%s ttl=%s",
                    session.session_id,
                    self.config.session_ttl,
                )

        await self._save_session(session)

        return session

    async def update_session(self, session: Session) -> None:
        """Update an existing session.

        Args:
            session: Session to update
        """
        session.update_last_accessed()
        await self._save_session(session)
        logger.debug("Session updated; id=%s", session.session_id)

    async def delete_session(self, session_id: str) -> None:
        """Delete a session.

        Args:
            session_id: Session ID to delete
        """
        key = self._get_backend_key(session_id)
        await self.backend.delete(key)
        logger.debug("Session deleted; id=%s", session_id)

    async def invalidate_session(self, session: Session) -> None:
        """Invalidate a session.

        Args:
            session: Session to invalidate
        """
        session.invalidate()
        await self._save_session(session)
        logger.debug("Session invalidated; id=%s", session.session_id)

    async def regenerate_session_id(
        self,
        session: Session,
    ) -> tuple[Session, str]:
        """Regenerate session ID (after login for security).

        Args:
            session: Session to regenerate

        Returns:
            Tuple of (updated session, new token string)
        """
        # Delete old session
        await self.delete_session(session.session_id)

        # Generate new ID
        old_id = session.session_id
        session.regenerate_id()

        # Save with new ID
        await self._save_session(session)

        # Create new token
        token = self._create_token(session.session_id)
        logger.debug(
            "Session ID regenerated; old_id=%s new_id=%s", old_id, session.session_id
        )

        return session, self._serializer.to_string(token)

    async def delete_user_sessions(self, user_id: str) -> int:
        """Delete all sessions for a user.

        Args:
            user_id: User ID

        Returns:
            Number of sessions deleted
        """
        # This requires scanning all session keys
        count = 0

        try:
            all_keys = await self.backend.get_all_keys()
            for key in all_keys:
                if key.startswith(self.config.backend_key_prefix):
                    session = await self._load_session_by_key(key)
                    if session and session.user and session.user.user_id == user_id:
                        await self.backend.delete(key)
                        count += 1
        except NotImplementedError:  # pragma: no cover
            # Backend doesn't support get_all_keys, can't delete by user
            pass

        logger.debug("User sessions deleted; user_id=%s count=%s", user_id, count)
        return count

    async def clear_expired_sessions(self) -> int:
        """Clear all expired sessions.

        Returns:
            Number of sessions cleared
        """
        count = 0

        try:
            all_keys = await self.backend.get_all_keys()
            for key in all_keys:
                if key.startswith(self.config.backend_key_prefix):
                    session = await self._load_session_by_key(key)
                    if session and session.is_expired():
                        await self.backend.delete(key)
                        count += 1
        except NotImplementedError:  # pragma: no cover
            # Backend doesn't support get_all_keys
            pass

        logger.debug("Expired sessions cleared; count=%s", count)
        return count

    def _create_token(self, session_id: str) -> SessionToken:
        """Create a signed session token.

        Args:
            session_id: Session ID to sign

        Returns:
            SessionToken object
        """
        # For 'simple' format, include HMAC signature in the token model.
        # For 'jwt' format, signature will be embedded in the JWT string; we can
        # leave the signature field empty as it won't be used downstream.
        signature = (
            self.security.sign_session_id(session_id)
            if self.config.token_format == "simple"
            else ""
        )
        return SessionToken(session_id=session_id, signature=signature)

    async def _save_session(self, session: Session) -> None:
        """Save session to backend.

        Args:
            session: Session to save
        """
        key = self._get_backend_key(session.session_id)
        value = session.model_dump_json().encode("utf-8")

        # Calculate TTL
        ttl = None
        if session.expires_at:
            ttl = int((session.expires_at - datetime.now(timezone.utc)).total_seconds())
            ttl = max(ttl, 1)  # Ensure at least 1 second

        # Store as bytes in cache backend (wrapped in ETagContent for compatibility)
        etag = self.security.hash_data(value.decode("utf-8"))
        await self.backend.set(key, ETagContent(etag=etag, content=value), ttl=ttl)
        logger.debug("Session saved; id=%s ttl=%s", session.session_id, ttl)

    async def _load_session(self, session_id: str) -> Session | None:
        """Load session from backend.

        Args:
            session_id: Session ID to load

        Returns:
            Session object or None if not found
        """
        key = self._get_backend_key(session_id)
        return await self._load_session_by_key(key)

    async def _load_session_by_key(self, key: str) -> Session | None:
        """Load session from backend by key.

        Args:
            key: Backend key

        Returns:
            Session object or None if not found
        """
        cached = await self.backend.get(key)
        if not cached:
            logger.debug("Session load MISS; key=%s", key)
            return None

        try:
            return Session.model_validate_json(cached.content)
        except (ValueError, TypeError):  # pragma: no cover
            # Invalid session data
            logger.debug("Session load DESERIALIZE ERROR; key=%s", key)
            return None
