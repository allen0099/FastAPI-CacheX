"""Shared fixtures for the session tests."""

import pytest

from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.session.config import SessionConfig
from fastapi_cachex.session.manager import SessionManager


@pytest.fixture
def backend() -> MemoryBackend:
    """Create a memory backend for testing."""
    return MemoryBackend()


@pytest.fixture
def config() -> SessionConfig:
    """Create session config for testing.

    The cookie settings are explicit so FastAPICacheXSessionMiddleware does not
    warn about the 0.4.0 cookie defaults (#256).
    """
    return SessionConfig(
        secret_key="a" * 32, cookie_name="session", cookie_https_only=False
    )


@pytest.fixture
def manager(backend: MemoryBackend, config: SessionConfig) -> SessionManager:
    """Create session manager for testing."""
    return SessionManager(backend, config)
