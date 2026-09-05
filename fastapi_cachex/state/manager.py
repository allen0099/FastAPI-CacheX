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
from fastapi_cachex.types import CacheEntry

from .exceptions import InvalidStateError
from .exceptions import StateDataError
from .exceptions import StateExpiredError
from .models import StateData

logger = logging.getLogger(__name__)

# Default TTL for OAuth state (10 minutes)
DEFAULT_STATE_TTL = 600


def _is_past(moment: datetime) -> bool:
    return datetime.now(timezone.utc) > moment


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

    def _cache_key(self, state: str) -> str:
        return f"{self.key_prefix}{state}"

    def _decode_state(self, cached: CacheEntry, state: str) -> StateData:
        """Turn a backend entry into a StateData model.

        Args:
            cached: The CacheEntry retrieved from backend
            state: State string (used for logging)

        Returns:
            StateData instance

        Raises:
            StateDataError: If the content is not UTF-8 text, not JSON, or does
                not fit the StateData model
        """
        try:
            json_content = cached.content.decode("utf-8")
        except (AttributeError, UnicodeDecodeError) as e:
            msg = "Unexpected state data format"
            raise StateDataError(msg) from e

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

    async def _peek_state(self, state: str) -> StateData | None:
        """Load a state without consuming it; None when missing, malformed or expired."""
        cached = await self.backend.get(self._cache_key(state))
        if cached is None:
            logger.debug("State not found; state=%s", state)
            return None

        try:
            state_data = self._decode_state(cached, state)
        except StateDataError:
            logger.exception("Failed to parse or validate state data; state=%s", state)
            return None

        if _is_past(state_data.expires_at):
            logger.debug("State expired; state=%s", state)
            return None

        return state_data

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

        content = state_data.model_dump_json().encode("utf-8")
        entry = CacheEntry(
            fingerprint=hashlib.sha256(content).hexdigest(), content=content
        )
        await self.backend.set(self._cache_key(state), entry, ttl=effective_ttl)

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
        # Take the state out of the backend atomically: of several concurrent
        # callers presenting the same state exactly one gets the entry, so a
        # replayed callback can never be accepted twice. An entry that turns
        # out to be malformed or past its wall-clock expiry is gone as well.
        cached = await self.backend.get_and_delete(self._cache_key(state))
        if cached is None:
            logger.warning("OAuth state not found or expired; state=%s", state)
            msg = "Invalid or expired state"
            raise InvalidStateError(msg)

        state_data = self._decode_state(cached, state)

        if _is_past(state_data.expires_at):
            logger.warning("OAuth state expired; state=%s", state)
            msg = "State has expired"
            raise StateExpiredError(msg)

        logger.debug("OAuth state consumed and deleted; state=%s", state)
        return state_data

    async def validate_state(self, state: str) -> bool:
        """Validate if a state exists and is not expired (without consuming it).

        Args:
            state: The state string to validate

        Returns:
            True if state is valid and not expired, False otherwise
        """
        return await self._peek_state(state) is not None

    async def get_state_metadata(self, state: str) -> dict[str, Any] | None:
        """Retrieve metadata for a state without consuming it.

        Args:
            state: The state string

        Returns:
            Metadata dictionary if state exists and is valid, None otherwise
        """
        state_data = await self._peek_state(state)
        return None if state_data is None else state_data.metadata

    async def delete_state(self, state: str) -> bool:
        """Manually delete a state from storage.

        Args:
            state: The state string to delete

        Returns:
            True if state was deleted, False if it didn't exist
        """
        if await self.backend.get_and_delete(self._cache_key(state)) is None:
            logger.debug("OAuth state not found for deletion; state=%s", state)
            return False
        logger.debug("OAuth state deleted; state=%s", state)
        return True
