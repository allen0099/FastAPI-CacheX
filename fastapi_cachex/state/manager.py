"""State manager for OAuth and session state handling."""

import hashlib
import json
import logging
import secrets
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.types import ETagContent

from .exceptions import InvalidStateError
from .exceptions import StateDataError
from .exceptions import StateExpiredError
from .models import StateData

logger = logging.getLogger(__name__)

# Default TTL for OAuth state (10 minutes)
DEFAULT_STATE_TTL = 600


class StateManager:
    """Manages OAuth state and session state lifecycle and storage."""

    def __init__(
        self,
        backend: BaseCacheBackend | None = None,
        key_prefix: str = "oauth_state:",
        default_ttl: int = DEFAULT_STATE_TTL,
    ) -> None:
        """Initialize StateManager.

        Args:
            backend: Cache backend instance. If None, uses BackendProxy.get().
            key_prefix: Prefix for state keys in cache backend
            default_ttl: Default time-to-live in seconds for state
        """
        self.backend = backend if backend is not None else BackendProxy.get()
        self.key_prefix = key_prefix
        self.default_ttl = default_ttl

    def _extract_json_content(self, cached: ETagContent) -> str:
        """Extract JSON string from a cached ETagContent value.

        Args:
            cached: The ETagContent retrieved from backend

        Returns:
            JSON string

        Raises:
            StateDataError: If the content cannot be decoded as UTF-8 text
        """
        try:
            return cached.content.decode("utf-8")
        except (AttributeError, UnicodeDecodeError) as e:
            msg = "Unexpected state data format"
            raise StateDataError(msg) from e

    def _parse_state_data(self, json_content: str, state: str) -> StateData:
        """Parse JSON content into a StateData model.

        Args:
            json_content: JSON string to parse
            state: State string (used for logging)

        Returns:
            StateData instance

        Raises:
            StateDataError: If parsing or validation fails
        """
        try:
            state_dict: dict[str, Any] = json.loads(json_content)
        except json.JSONDecodeError as e:
            msg = f"Failed to parse state data: {e}"
            logger.exception("Failed to parse state data; state=%s", state)
            raise StateDataError(msg) from e

        try:
            return StateData(**state_dict)
        except ValueError as e:
            msg = f"Invalid state data structure: {e}"
            logger.exception("Failed to create StateData model; state=%s", state)
            raise StateDataError(msg) from e

    async def create_state(
        self,
        ttl: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new random OAuth state and store it with metadata.

        Args:
            ttl: Time-to-live in seconds (uses default_ttl if not provided)
            metadata: Additional metadata to store with the state (e.g., callback_url, user_info)

        Returns:
            The generated state string

        Raises:
            StateDataError: If backend storage fails
        """
        # Generate a random state string (32 bytes = 256 bits of entropy)
        state = secrets.token_urlsafe(32)

        # Use provided TTL or default
        effective_ttl = ttl if ttl is not None else self.default_ttl

        # Create state data model
        state_data = StateData(
            state=state,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=effective_ttl),
            metadata=metadata or {},
        )

        # Serialize to JSON
        json_content = json.dumps(state_data.model_dump(mode="json"))

        # Create ETag from hash of state data
        etag = hashlib.sha256(json_content.encode()).hexdigest()

        # Store in backend with TTL using ETagContent
        cache_key = f"{self.key_prefix}{state}"
        etag_content = ETagContent(etag=etag, content=json_content.encode("utf-8"))
        await self.backend.set(cache_key, etag_content, ttl=effective_ttl)

        logger.debug("OAuth state created; state=%s ttl=%s", state, effective_ttl)
        return state

    async def consume_state(self, state: str) -> StateData:
        """Consume and validate an OAuth state, removing it from storage.

        Args:
            state: The state string to validate and consume

        Returns:
            StateData object containing state data and metadata

        Raises:
            InvalidStateError: If state is invalid or not found
            StateExpiredError: If state has expired
            StateDataError: If state data format is invalid
        """
        cache_key = f"{self.key_prefix}{state}"

        # Retrieve state data from backend
        cached_etag_content = await self.backend.get(cache_key)
        if cached_etag_content is None:
            logger.warning("OAuth state not found or expired; state=%s", state)
            msg = "Invalid or expired state"
            raise InvalidStateError(msg)

        json_content = self._extract_json_content(cached_etag_content)
        state_data = self._parse_state_data(json_content, state)

        # Verify expiry
        if datetime.now(timezone.utc) > state_data.expires_at:
            logger.warning("OAuth state expired; state=%s", state)
            msg = "State has expired"
            raise StateExpiredError(msg)

        # Delete the state from backend to prevent reuse
        await self.backend.delete(cache_key)
        logger.debug("OAuth state consumed and deleted; state=%s", state)

        return state_data

    async def validate_state(self, state: str) -> bool:
        """Validate if a state exists and is not expired (without consuming it).

        Args:
            state: The state string to validate

        Returns:
            True if state is valid and not expired, False otherwise
        """
        cache_key = f"{self.key_prefix}{state}"

        cached_etag_content = await self.backend.get(cache_key)
        if cached_etag_content is None:
            logger.debug("State validation failed - not found; state=%s", state)
            return False

        try:
            json_content = self._extract_json_content(cached_etag_content)
            state_data = self._parse_state_data(json_content, state)
        except (StateDataError, UnicodeDecodeError):
            logger.exception(
                "Failed to parse or validate state data; state=%s",
                state,
            )
            return False

        if datetime.now(timezone.utc) > state_data.expires_at:
            logger.debug("State validation failed - expired; state=%s", state)
            return False

        logger.debug("State validation succeeded; state=%s", state)
        return True

    async def get_state_metadata(self, state: str) -> dict[str, Any] | None:
        """Retrieve metadata for a state without consuming it.

        Args:
            state: The state string

        Returns:
            Metadata dictionary if state exists and is valid, None otherwise
        """
        cache_key = f"{self.key_prefix}{state}"

        cached_etag_content = await self.backend.get(cache_key)
        if cached_etag_content is None:
            return None

        try:
            json_content = self._extract_json_content(cached_etag_content)
            state_data = self._parse_state_data(json_content, state)
        except (StateDataError, UnicodeDecodeError):
            logger.exception("Failed to parse or validate state data; state=%s", state)
            return None

        if datetime.now(timezone.utc) > state_data.expires_at:
            return None

        return state_data.metadata

    async def delete_state(self, state: str) -> bool:
        """Manually delete a state from storage.

        Args:
            state: The state string to delete

        Returns:
            True if state was deleted, False if it didn't exist
        """
        cache_key = f"{self.key_prefix}{state}"
        # Check existence before deleting to return accurate result
        existing = await self.backend.get(cache_key)
        if existing is None:
            logger.debug("OAuth state not found for deletion; state=%s", state)
            return False
        await self.backend.delete(cache_key)
        logger.debug("OAuth state deleted; state=%s", state)
        return True
