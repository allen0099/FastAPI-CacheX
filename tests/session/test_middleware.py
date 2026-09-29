"""Tests for client address resolution and header token extraction."""

import pytest
from fastapi import Request

from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.middleware import _read_header_token
from fastapi_cachex.session.middleware import get_client_ip

# `get_client_ip` is the address resolution the middleware binds sessions to.


def test_get_client_ip_ignores_forwarded_headers_by_default(
    config: SessionConfig,
) -> None:
    """Forwarded headers are spoofable, so an untrusted peer's are ignored."""
    connection = _connection(
        {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"}, peer="10.0.0.9"
    )

    assert get_client_ip(connection, config) == "10.0.0.9"


def test_get_client_ip_from_x_forwarded_for_behind_trusted_proxy() -> None:
    """A proxy the app vouches for may report the real client address."""
    config = SessionConfig(
        secret_key="a" * 32, trusted_proxies=["10.0.0.9", "10.0.0.1"]
    )
    connection = _connection(
        {"X-Forwarded-For": "192.168.1.1, 10.0.0.1"}, peer="10.0.0.9"
    )

    assert get_client_ip(connection, config) == "192.168.1.1"


def test_get_client_ip_ignores_a_prepended_forwarded_entry() -> None:
    """Proxies append, so the leftmost entry is whatever the caller sent."""
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.9"])
    # The attacker sent the first entry themselves; nginx appended the second.
    connection = _connection(
        {"X-Forwarded-For": "198.51.100.5, 203.0.113.99"}, peer="10.0.0.9"
    )

    assert get_client_ip(connection, config) == "203.0.113.99"


def test_get_client_ip_falls_back_when_every_hop_is_trusted() -> None:
    """With no untrusted entry left there is no client address to recover."""
    config = SessionConfig(
        secret_key="a" * 32, trusted_proxies=["10.0.0.9", "10.0.0.1"]
    )
    connection = _connection({"X-Forwarded-For": "10.0.0.1"}, peer="10.0.0.9")

    assert get_client_ip(connection, config) == "10.0.0.9"


def test_get_client_ip_from_real_ip_behind_trusted_proxy() -> None:
    """X-Real-IP is the fallback once the peer is trusted."""
    config = SessionConfig(secret_key="a" * 32, trusted_proxies=["10.0.0.9"])
    connection = _connection({"X-Real-IP": "192.168.1.1"}, peer="10.0.0.9")

    assert get_client_ip(connection, config) == "192.168.1.1"


def test_get_client_ip_from_client(config: SessionConfig) -> None:
    connection = _connection({}, peer="192.168.1.1")

    assert get_client_ip(connection, config) == "192.168.1.1"


def test_get_client_ip_none(config: SessionConfig) -> None:
    """No peer and no trusted forwarding: there is no address."""
    assert get_client_ip(_connection({}), config) is None


def _connection(headers: dict[str, str], peer: str | None = None) -> Request:
    """A bare `Request` carrying only the headers (and peer) under test."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": (peer, 1234) if peer is not None else None,
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in headers.items()
            ],
        }
    )


def test_bearer_is_used_when_the_header_source_finds_nothing(
    config: SessionConfig,
) -> None:
    """The priority list is a fallback chain, not a first-entry-only lookup.

    Every other extraction test supplies the header it asks for first, so the
    loop always returned on its first pass and an implementation that only
    ever checked `token_source_priority[0]` would have passed them all.
    """
    token = _read_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )[0]

    assert token == "from-bearer"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer from-bearer",
        "bearer from-bearer",
        "BEARER from-bearer",
        "Bearer   from-bearer",
    ],
)
def test_bearer_scheme_is_matched_case_insensitively(
    config: SessionConfig, authorization: str
) -> None:
    """Auth schemes are case-insensitive (RFC 9110 §11.1), and RFC 6750 allows
    more than one space before the token (#166).
    """
    token = _read_header_token(_connection({"Authorization": authorization}), config)[0]

    assert token == "from-bearer"


@pytest.mark.parametrize(
    "authorization",
    ["Bearer", "Bearer ", "Bearer   ", "Basic from-bearer", "Bearerfrom-bearer"],
)
def test_an_empty_or_non_bearer_authorization_header_yields_no_token(
    config: SessionConfig, authorization: str
) -> None:
    token = _read_header_token(_connection({"Authorization": authorization}), config)[0]

    assert token is None


def test_header_wins_over_bearer_when_both_are_present(
    config: SessionConfig,
) -> None:
    """Order in the list is the order that is honoured."""
    token = _read_header_token(
        _connection(
            {
                config.header_name: "from-header",
                "Authorization": "Bearer from-bearer",
            }
        ),
        config,
    )[0]

    assert token == "from-header"


def test_bearer_source_is_skipped_when_bearer_tokens_are_disabled() -> None:
    """`use_bearer_token=False` must win over the priority list."""
    with pytest.warns(DeprecationWarning, match="use_bearer_token"):
        config = SessionConfig(secret_key="a" * 32, use_bearer_token=False)

    token = _read_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )[0]

    assert token is None


def test_an_unknown_source_is_skipped_rather_than_read_as_a_bearer_token() -> None:
    """The chain ends in an `elif`, not an `else`, and this is why.

    `token_source_priority` is a list of Literals, so pydantic refuses an
    unknown source at construction and no caller can reach this through the
    public API. Nothing revalidates the list afterwards, though, and the
    branch exists for the maintainer who adds a third source to the Literal
    and forgets this function: it must fall through, not inherit whatever the
    last branch happens to do. Mutating the list in place is the only way to
    stand where that maintainer will stand.
    """
    config = SessionConfig(secret_key="a" * 32)
    config.token_source_priority[:] = ["query"]  # type: ignore[list-item]

    token = _read_header_token(
        _connection({"Authorization": "Bearer from-bearer"}), config
    )[0]

    assert token is None


def test_a_known_source_after_an_unknown_one_is_still_honoured(
    config: SessionConfig,
) -> None:
    """Falling through must continue the chain, not abandon it."""
    config.token_source_priority[:] = ["query", "header"]  # type: ignore[list-item]

    token = _read_header_token(
        _connection({config.header_name: "from-header"}), config
    )[0]

    assert token == "from-header"
