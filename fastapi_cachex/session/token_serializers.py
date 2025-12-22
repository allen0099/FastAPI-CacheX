"""Token serializer strategies for session tokens.

Provides a simple serializer compatible with the existing
"session_id.signature.timestamp" format, and an optional JWT serializer.

The JWT serializer supports dependency injection to allow swapping in
compatible JWT backends (e.g., PyJWT or another library exposing
``encode``/``decode`` with similar signatures).
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime
from datetime import timezone
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from .models import SessionToken

if TYPE_CHECKING:  # Import for typing only to avoid circular import concerns
    from .config import SessionConfig

logger = logging.getLogger(__name__)


class TokenSerializer(Protocol):
    """Protocol for token serialization strategies."""

    def to_string(self, token: SessionToken) -> str:  # pragma: no cover - interface
        """Serialize a `SessionToken` to a string."""

    def from_string(
        self, token_str: str
    ) -> SessionToken:  # pragma: no cover - interface
        """Parse a string into a `SessionToken` (with necessary verification)."""


class SimpleTokenSerializer:
    """Serializer for the default simple token format."""

    def to_string(self, token: SessionToken) -> str:
        """Serialize using the built-in simple format."""
        return token.to_string()

    def from_string(self, token_str: str) -> SessionToken:
        """Parse the built-in simple token format."""
        return SessionToken.from_string(token_str)


class JWTTokenSerializer:
    """JWT-based token serializer.

    Encodes the session reference into a signed JWT with claims:
    - sid: session id (custom claim)
    - iat: issued at (epoch seconds)
    - exp: expiry (epoch seconds), derived from config.session_ttl
    Optionally:
    - iss: issuer (if configured)
    - aud: audience (if configured)
    """

    def __init__(self, config: SessionConfig, jwt_module: Any | None = None) -> None:
        """Initialize a JWT-based serializer with optional backend injection.

        Args:
            config: Session configuration instance.
            jwt_module: Optional JWT-compatible module providing ``encode`` and
                ``decode``; defaults to importing ``jwt`` (PyJWT).
        """
        if jwt_module is not None:
            self.jwt_encoder = jwt_module  # pragma: no cover
        else:
            try:
                self.jwt_encoder = importlib.import_module("jwt")
            except ImportError as e:  # pragma: no cover
                msg = "JWT backend not available; install fastapi-cachex[jwt] or inject jwt_module"
                raise ImportError(msg) from e

        # Copy required parameters
        self._secret = config.secret_key.get_secret_value()
        self._algorithm = config.jwt_algorithm
        self._issuer = config.jwt_issuer
        self._audience = config.jwt_audience
        self._leeway = config.jwt_leeway
        self._session_ttl = config.session_ttl

    def to_string(self, token: SessionToken) -> str:
        """Encode a `SessionToken` as a signed JWT string.

        Uses claims `sid`, `iat`, `exp`, and optional `iss`/`aud`.
        """
        iat = int(token.issued_at.timestamp())
        exp = iat + int(self._session_ttl)

        payload: dict[str, object] = {
            "sid": token.session_id,
            "iat": iat,
            "exp": exp,
        }
        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience

        encoded = self.jwt_encoder.encode(
            payload, self._secret, algorithm=self._algorithm
        )
        logger.debug("JWT token encoded; sid=%s", token.session_id)
        # PyJWT may return str in PyJWT>=2, ensure str
        return str(encoded)

    def from_string(self, token_str: str) -> SessionToken:
        """Decode and verify a JWT string into a `SessionToken`.

        Verifies signature, `exp`, and `iat`, and optional `iss`/`aud`.
        """
        options = {
            "require": ["sid", "iat", "exp"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_iat": True,
        }

        kwargs: dict[str, object] = {
            "algorithms": [self._algorithm],
            "options": options,
            "leeway": self._leeway,
            "key": self._secret,
        }

        if self._issuer:
            kwargs["issuer"] = self._issuer
        if self._audience:
            kwargs["audience"] = self._audience

        try:
            payload = self.jwt_encoder.decode(token_str, **kwargs)
        except Exception as e:  # Broad catch to normalize to ValueError
            logger.debug("JWT decode failed: %s", e)
            msg = "Invalid JWT token"
            raise ValueError(msg) from e

        try:
            raw_sid = payload["sid"]
            raw_iat = payload["iat"]
        except Exception as e:
            msg = "Invalid JWT payload"
            raise ValueError(msg) from e

        # Normalize types for mypy and runtime safety
        sid = raw_sid if isinstance(raw_sid, str) else str(raw_sid)
        if isinstance(raw_iat, int):
            iat = raw_iat
        else:
            try:
                iat = int(raw_iat)
            except Exception as e:
                msg = "Invalid JWT payload"
                raise ValueError(msg) from e

        issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)
        # For JWT path, signature is not used in downstream verification
        return SessionToken(session_id=sid, signature="", issued_at=issued_at)
