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
    """Create session config for testing."""
    return SessionConfig(secret_key="a" * 32)


@pytest.fixture
def manager(backend: MemoryBackend, config: SessionConfig) -> SessionManager:
    """Create session manager for testing."""
    return SessionManager(backend, config)
