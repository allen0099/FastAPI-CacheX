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
import warnings
from datetime import datetime
from datetime import timezone
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from .config import JWT_HMAC_ALGORITHMS
from .models import SessionToken

if TYPE_CHECKING:  # Import for typing only to avoid circular import concerns
    from .config import SessionConfig

logger = logging.getLogger(__name__)

# Token format constant - 3 parts: session_id, signature, timestamp
TOKEN_PARTS_COUNT = 3

# RFC 7518 section 3.2: an HMAC key must be at least as long as the hash output.
_HMAC_MIN_KEY_BYTES = {"HS256": 32, "HS384": 48, "HS512": 64}


class TokenSerializer(Protocol):
    """Protocol for token serialization strategies."""

    def to_string(self, token: SessionToken) -> str:  # pragma: no cover - Protocol body
        """Serialize a `SessionToken` to a string."""

    def from_string(
        self, token_str: str
    ) -> SessionToken:  # pragma: no cover - Protocol body
        """Parse a string into a `SessionToken` (with necessary verification)."""


class SimpleTokenSerializer:
    """Serializer for the default simple token format."""

    def to_string(self, token: SessionToken) -> str:
        """Convert token to string format.

        Format: {session_id}.{signature}.{timestamp}

        Args:
            token: SessionToken instance to serialize

        Returns:
            Token string in format {session_id}.{signature}.{timestamp}
        """
        timestamp = int(token.issued_at.timestamp())

        logger.debug("SimpleTokenSerializer to_string called; id=%s", token.session_id)
        return f"{token.session_id}.{token.signature}.{timestamp}"

    def from_string(self, token_str: str) -> SessionToken:
        """Parse token from string format.

        Args:
            token_str: Token string in format {session_id}.{signature}.{timestamp}

        Returns:
            SessionToken instance

        Raises:
            ValueError: If token format is invalid
        """
        parts = token_str.split(".")
        if len(parts) != TOKEN_PARTS_COUNT:
            msg = "Invalid token format"
            raise ValueError(msg)

        session_id, signature, timestamp = parts
        try:
            issued_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        except (ValueError, OSError, OverflowError) as e:
            msg = f"Invalid timestamp in token: {e}"
            raise ValueError(msg) from e

        logger.debug("SimpleTokenSerializer parsed from string; id=%s", session_id)
        return SessionToken(
            session_id=session_id, signature=signature, issued_at=issued_at
        )


def _warn_if_key_too_short(secret: str, algorithm: str) -> None:
    """Warn once when ``secret`` is shorter than ``algorithm``'s hash output.

    ``SessionConfig`` only requires 32 characters, enough for HS256 but not
    for HS384 (48 bytes) or HS512 (64 bytes). PyJWT warns about a short key on
    every token it signs or verifies; this names the setting to fix, once,
    when the serializer is built.
    """
    min_bytes = _HMAC_MIN_KEY_BYTES[algorithm]
    key_bytes = len(secret.encode("utf-8"))
    if key_bytes < min_bytes:
        warnings.warn(
            f"secret_key is {key_bytes} bytes, shorter than the {min_bytes} "
            f"bytes RFC 7518 section 3.2 requires for {algorithm}. Use a longer "
            f"secret_key (e.g. secrets.token_urlsafe({min_bytes})) or "
            f'jwt_algorithm="HS256".',
            UserWarning,
            stacklevel=3,
        )


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

        Raises:
            ValueError: If ``config.jwt_algorithm`` is asymmetric. This
                serializer signs and verifies with the ``secret_key`` string,
                which only the HMAC algorithms can use; an asymmetric
                algorithm needs a custom ``token_serializer`` that holds the
                key pair.
        """
        if config.jwt_algorithm not in JWT_HMAC_ALGORITHMS:
            supported = ", ".join(sorted(JWT_HMAC_ALGORITHMS))
            msg = (
                f"jwt_algorithm {config.jwt_algorithm!r} needs a private/public "
                f"key pair, but the built-in JWT serializer signs with secret_key; "
                f"use one of {supported}, or pass a custom token_serializer to "
                f"SessionManager"
            )
            raise ValueError(msg)

        if jwt_module is not None:
            self.jwt_encoder = jwt_module
        else:
            try:
                self.jwt_encoder = importlib.import_module("jwt")
            except ImportError as e:
                msg = "JWT backend not available; install fastapi-cachex[jwt] or inject jwt_module"
                raise ImportError(msg) from e

        # Copy required parameters
        self._secret = config.secret_key.get_secret_value()
        _warn_if_key_too_short(self._secret, config.jwt_algorithm)
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
        # Use session's expires_at when available (supports sliding expiration)
        if token.expires_at is not None:
            exp = int(token.expires_at.timestamp())
        else:
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
