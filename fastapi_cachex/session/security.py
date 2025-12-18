"""Security utilities for session management."""

import hashlib
import hmac

from fastapi_cachex.session.models import Session


class SecurityManager:
    """Handles session security operations."""

    def __init__(self, secret_key: str) -> None:
        """Initialize security manager.

        Args:
            secret_key: Secret key for signing tokens
        """
        if len(secret_key) < 32:  # noqa: PLR2004
            msg = "Secret key must be at least 32 characters"
            raise ValueError(msg)
        self.secret_key = secret_key.encode("utf-8")

    def sign_session_id(self, session_id: str) -> str:
        """Sign a session ID using HMAC-SHA256.

        Args:
            session_id: The session ID to sign

        Returns:
            The signature as a hex string
        """
        return hmac.new(
            self.secret_key,
            session_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_signature(self, session_id: str, signature: str) -> bool:
        """Verify a session signature.

        Args:
            session_id: The session ID
            signature: The signature to verify

        Returns:
            True if signature is valid, False otherwise
        """
        expected_signature = self.sign_session_id(session_id)
        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(expected_signature, signature)

    def check_ip_match(self, session: Session, current_ip: str | None) -> bool:
        """Check if session IP matches current request IP.

        Args:
            session: The session to check
            current_ip: Current request IP address

        Returns:
            True if IPs match or session has no IP binding
        """
        if session.ip_address is None:
            return True
        if current_ip is None:
            return False
        return session.ip_address == current_ip

    def check_user_agent_match(
        self,
        session: Session,
        current_user_agent: str | None,
    ) -> bool:
        """Check if session User-Agent matches current request.

        Args:
            session: The session to check
            current_user_agent: Current request User-Agent

        Returns:
            True if User-Agents match or session has no UA binding
        """
        if session.user_agent is None:
            return True
        if current_user_agent is None:
            return False
        return session.user_agent == current_user_agent

    def hash_data(self, data: str) -> str:
        """Hash data using SHA-256.

        Args:
            data: Data to hash

        Returns:
            Hex digest of the hash
        """
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
