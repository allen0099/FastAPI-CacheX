"""Tests for SessionConfig validation."""

import warnings

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
        SessionConfig(secret_key="a" * 32, regenerate_on_login=True)  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        SessionConfig(secret_key="a" * 32, enable_csrf=True)  # type: ignore[call-arg]


def test_same_site_none_without_https_only_warns() -> None:
    """Browsers drop a SameSite=None cookie that is not Secure (#167)."""
    with pytest.warns(UserWarning, match='cookie_same_site="none"'):
        SessionConfig(secret_key="a" * 32, cookie_same_site="none")


@pytest.mark.parametrize(
    ("same_site", "https_only"),
    [("none", True), ("lax", False), ("strict", False)],
)
def test_other_cookie_settings_do_not_warn(same_site, https_only) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SessionConfig(
            secret_key="a" * 32,
            cookie_same_site=same_site,
            cookie_https_only=https_only,
        )
