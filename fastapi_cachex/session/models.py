"""Session data models and user structures."""

import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    """Session status enumeration."""

    ACTIVE = "active"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class SessionUser(BaseModel):
    """Base session user model.

    This can be extended by application-specific user models.
    """

    user_id: str
    username: str | None = None
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class Session(BaseModel):
    """Core session model containing all session data."""

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    user: SessionUser | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    ip_address: str | None = None
    user_agent: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    flash_messages: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"use_enum_values": True}

    def is_valid(self) -> bool:
        """Check if session is valid (active and not expired)."""
        if self.status != SessionStatus.ACTIVE:
            return False

        return not (self.expires_at and datetime.now(timezone.utc) > self.expires_at)

    def is_expired(self) -> bool:
        """Check if session has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def update_last_accessed(self) -> None:
        """Update the last accessed timestamp."""
        self.last_accessed = datetime.now(timezone.utc)
        logger.debug("Session last_accessed updated; id=%s", self.session_id)

    def renew(self, ttl: int) -> None:
        """Renew session expiry time.

        Args:
            ttl: Time-to-live in seconds
        """
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        self.update_last_accessed()
        logger.debug("Session renewed; id=%s ttl=%s", self.session_id, ttl)

    def invalidate(self) -> None:
        """Mark session as invalidated."""
        self.status = SessionStatus.INVALIDATED
        logger.debug("Session invalidated; id=%s", self.session_id)

    def regenerate_id(self) -> str:
        """Regenerate session ID (for security after login).

        Returns:
            The new session ID
        """
        old_id = self.session_id
        self.session_id = str(uuid4())
        logger.debug(
            "Session ID regenerated; old_id=%s new_id=%s", old_id, self.session_id
        )
        return self.session_id

    def add_flash_message(self, message: str, category: str = "info") -> None:
        """Add a flash message.

        Args:
            message: The message content
            category: Message category (info, success, warning, error)
        """
        self.flash_messages.append(
            {
                "message": message,
                "category": category,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.debug(
            "Flash message added; id=%s category=%s", self.session_id, category
        )

    def get_flash_messages(self, clear: bool = True) -> list[dict[str, Any]]:
        """Get and optionally clear flash messages.

        Args:
            clear: Whether to clear messages after retrieving

        Returns:
            List of flash messages
        """
        messages = self.flash_messages.copy()
        if clear:
            self.flash_messages.clear()
            logger.debug(
                "Flash messages cleared; id=%s count=%s", self.session_id, len(messages)
            )
        return messages


class SessionToken(BaseModel):
    """Session token containing signed data."""

    session_id: str
    signature: str
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
