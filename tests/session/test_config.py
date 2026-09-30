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
        SessionConfig(
            secret_key="a" * 32,
            cookie_name="session",
            cookie_same_site="none",
            cookie_https_only=False,
        )


@pytest.mark.parametrize(
    ("same_site", "https_only"),
    [("none", True), ("lax", False), ("strict", False)],
)
def test_other_cookie_settings_do_not_warn(same_site, https_only) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SessionConfig(
            secret_key="a" * 32,
            cookie_name="session",
            cookie_same_site=same_site,
            cookie_https_only=https_only,
        )


# --- Cookie prefixes (#256) -----------------------------------------------------


_PLAIN_HTTP_HINT = "cookie_name='session' with cookie_https_only=False"
_SECURE_PREFIX_HINT = "use cookie_name='__Secure-session', which keeps the Secure flag"


@pytest.mark.parametrize(
    ("settings", "requirements", "hints"),
    [
        (
            {"cookie_https_only": False},
            ["cookie_https_only=True"],
            [_PLAIN_HTTP_HINT],
        ),
        (
            {"cookie_name": "__Secure-session", "cookie_https_only": False},
            ["cookie_https_only=True"],
            [_PLAIN_HTTP_HINT],
        ),
        ({"cookie_path": "/app"}, ['cookie_path="/"'], [_SECURE_PREFIX_HINT]),
        (
            {"cookie_domain": "example.com"},
            ["cookie_domain=None"],
            [_SECURE_PREFIX_HINT],
        ),
        (
            {"cookie_https_only": False, "cookie_path": "/app"},
            ["cookie_https_only=True", 'cookie_path="/"'],
            [_PLAIN_HTTP_HINT, _SECURE_PREFIX_HINT],
        ),
    ],
)
def test_prefixed_cookie_names_browsers_refuse_are_rejected(
    settings: dict[str, object], requirements: list[str], hints: list[str]
) -> None:
    """A cookie browsers would refuse never sticks, so the config is an error.

    The default name is ``__Host-session``, so changing only the Secure flag,
    path or domain is enough to hit this, and the message says the name is
    the default. The hint fits the problem: dropping Secure is advice for
    plain HTTP only, not for someone setting a path or domain.
    """
    with pytest.raises(ValidationError, match="cookie_name=") as excinfo:
        SessionConfig(secret_key="a" * 32, **settings)

    message = str(excinfo.value)
    for requirement in requirements:
        assert requirement in message
    for hint in (_PLAIN_HTTP_HINT, _SECURE_PREFIX_HINT):
        assert (hint in message) is (hint in hints)
    assert ("(the default)" in message) is ("cookie_name" not in settings)
    assert "SESSION/#cookie-defaults" in message


def test_same_site_none_with_a_prefixed_name_is_only_rejected() -> None:
    """The prefix check covers the missing Secure flag; no extra warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValidationError, match="cookie_https_only=True"):
            SessionConfig(
                secret_key="a" * 32,
                cookie_same_site="none",
                cookie_https_only=False,
            )


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {
            "cookie_name": "__Secure-session",
            "cookie_path": "/app",
            "cookie_domain": "example.com",
        },
        {
            "cookie_name": "session",
            "cookie_https_only": False,
            "cookie_path": "/app",
            "cookie_domain": "example.com",
        },
    ],
)
def test_valid_cookie_prefixes_are_accepted(settings: dict[str, object]) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SessionConfig(secret_key="a" * 32, **settings)
