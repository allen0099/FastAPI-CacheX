"""Session manager for CRUD operations."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.exceptions import SessionExpiredError
from fastapi_cachex.session.exceptions import SessionInvalidError
from fastapi_cachex.session.exceptions import SessionNotFoundError
from fastapi_cachex.session.exceptions import SessionSecurityError
from fastapi_cachex.session.exceptions import SessionTokenError
from fastapi_cachex.session.models import Session
from fastapi_cachex.session.models import SessionStatus
from fastapi_cachex.session.models import SessionToken
from fastapi_cachex.session.models import SessionUser
from fastapi_cachex.session.security import SecurityManager
from fastapi_cachex.types import ETagContent


class SessionManager:
    """Manages session lifecycle and storage."""

    def __init__(self, backend: BaseCacheBackend, config: SessionConfig) -> None:
        """Initialize session manager.

        Args:
            backend: Cache backend for session storage
            config: Session configuration
        """
        self.backend = backend
        self.config = config
        self.security = SecurityManager(config.secret_key)

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
        user: SessionUser | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        **extra_data: dict[str, object],
    ) -> tuple[Session, str]:
        """Create a new session.

        Args:
            user: Optional user data
            ip_address: Client IP address (if IP binding enabled)
            user_agent: Client User-Agent (if UA binding enabled)
            **extra_data: Additional session data

        Returns:
            Tuple of (Session, token_string)
        """
        # Create session
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

        return session, token.to_string()

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
            token = SessionToken.from_string(token_string)
        except ValueError as e:
            raise SessionTokenError(str(e)) from e

        if not self.security.verify_signature(token.session_id, token.signature):
            msg = "Invalid session signature"
            raise SessionSecurityError(msg)

        # Load session from backend
        session = await self._load_session(token.session_id)
        if not session:
            msg = f"Session {token.session_id} not found"
            raise SessionNotFoundError(msg)

        # Validate session
        if session.status != SessionStatus.ACTIVE:
            msg = f"Session is {session.status}"
            raise SessionInvalidError(msg)

        if session.is_expired():
            session.status = SessionStatus.EXPIRED
            await self._save_session(session)
            msg = "Session has expired"
            raise SessionExpiredError(msg)

        # Security checks
        if self.config.ip_binding and not self.security.check_ip_match(
            session,
            ip_address,
        ):
            msg = "IP address mismatch"
            raise SessionSecurityError(msg)

        if self.config.user_agent_binding and not self.security.check_user_agent_match(
            session,
            user_agent,
        ):
            msg = "User-Agent mismatch"
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

        await self._save_session(session)

        return session

    async def update_session(self, session: Session) -> None:
        """Update an existing session.

        Args:
            session: Session to update
        """
        session.update_last_accessed()
        await self._save_session(session)

    async def delete_session(self, session_id: str) -> None:
        """Delete a session.

        Args:
            session_id: Session ID to delete
        """
        key = self._get_backend_key(session_id)
        await self.backend.delete(key)

    async def invalidate_session(self, session: Session) -> None:
        """Invalidate a session.

        Args:
            session: Session to invalidate
        """
        session.invalidate()
        await self._save_session(session)

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
        session.regenerate_id()

        # Save with new ID
        await self._save_session(session)

        # Create new token
        token = self._create_token(session.session_id)

        return session, token.to_string()

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

        return count

    def _create_token(self, session_id: str) -> SessionToken:
        """Create a signed session token.

        Args:
            session_id: Session ID to sign

        Returns:
            SessionToken object
        """
        signature = self.security.sign_session_id(session_id)
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
            return None

        try:
            return Session.model_validate_json(cached.content)
        except (ValueError, TypeError):  # pragma: no cover
            # Invalid session data
            return None
