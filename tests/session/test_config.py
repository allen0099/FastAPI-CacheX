"""Tests for SessionConfig validation."""

import pytest
from pydantic import ValidationError

from fastapi_cachex.session.config import SessionConfig


def test_session_config_accepts_known_fields() -> None:
    config = SessionConfig(secret_key="a" * 32, session_ttl=1800)
    assert config.session_ttl == 1800


def test_session_config_rejects_unknown_fields() -> None:
    """Unknown/misspelled fields must raise, not be silently dropped.

    Regression test: SessionConfig previously used pydantic's default
    extra="ignore", so passing a nonexistent option like the docs' former
    ``regenerate_on_login``/``enable_csrf`` examples silently did nothing
    instead of surfacing a startup-time error.
    """
    with pytest.raises(ValidationError):
        SessionConfig(secret_key="a" * 32, regenerate_on_login=True)

    with pytest.raises(ValidationError):
        SessionConfig(secret_key="a" * 32, enable_csrf=True)
