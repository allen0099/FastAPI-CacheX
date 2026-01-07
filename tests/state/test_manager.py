"""Tests for OAuth state manager functionality."""

import hashlib
import json
import socket
from collections.abc import AsyncGenerator
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import pytest
import pytest_asyncio

from fastapi_cachex.backends.base import BaseCacheBackend
from fastapi_cachex.backends.memory import MemoryBackend
from fastapi_cachex.proxy import BackendProxy
from fastapi_cachex.state.exceptions import InvalidStateError
from fastapi_cachex.state.exceptions import StateDataError
from fastapi_cachex.state.manager import StateManager
from fastapi_cachex.state.models import StateData
from fastapi_cachex.types import ETagContent


def is_redis_running(host: str = "127.0.0.1", port: int = 6379) -> bool:
    """Check if Redis server is running."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect((host, port))
        s.close()
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False
    else:
        return True


def has_redis_package() -> bool:
    """Return True if the redis package is importable."""
    try:
        import redis.asyncio  # type: ignore[unused-ignore]  # noqa: F401

    except Exception:
        return False
    return True


@pytest_asyncio.fixture
async def memory_backend_for_state() -> AsyncGenerator[BaseCacheBackend, Any]:
    """Create a MemoryBackend instance."""
    backend = MemoryBackend()
    backend.start_cleanup()
    yield backend
    backend.stop_cleanup()


@pytest_asyncio.fixture
async def redis_backend_for_state() -> AsyncGenerator[BaseCacheBackend, Any]:
    """Create a Redis backend instance if Redis is available."""
    if not is_redis_running() or not has_redis_package():
        pytest.skip("Redis server is not running or redis package not installed")

    from fastapi_cachex.backends import AsyncRedisCacheBackend

    backend = AsyncRedisCacheBackend(
        host="127.0.0.1",
        port=6379,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
        key_prefix="test_state:",
    )
    yield backend
    await backend.clear()


@pytest_asyncio.fixture(
    params=[
        pytest.param("memory", id="MemoryBackend"),
        pytest.param(
            "redis",
            id="RedisBackend",
            marks=pytest.mark.skipif(
                not (is_redis_running() and has_redis_package()),
                reason="Redis not available",
            ),
        ),
    ]
)
async def state_manager(
    request: Any,
) -> AsyncGenerator[StateManager, Any]:
    """Create a StateManager instance with different backends.

    This fixture is parametrized to test with:
    - MemoryBackend (always runs)
    - RedisBackend (runs only if Redis is available)
    """
    backend_type = request.param

    if backend_type == "memory":
        mem_backend = MemoryBackend()
        mem_backend.start_cleanup()
        backend: BaseCacheBackend = mem_backend
    else:  # redis
        from fastapi_cachex.backends import AsyncRedisCacheBackend

        backend = AsyncRedisCacheBackend(
            host="127.0.0.1",
            port=6379,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            key_prefix="test_state:",
        )

    BackendProxy.set_backend(backend)
    manager = StateManager()

    yield manager

    # Cleanup
    await backend.clear()
    if backend_type == "memory" and isinstance(backend, MemoryBackend):
        backend.stop_cleanup()


@pytest.mark.asyncio
async def test_create_state_basic(state_manager: StateManager) -> None:
    """Test creating a basic OAuth state."""
    state = await state_manager.create_state()

    assert state is not None
    assert isinstance(state, str)
    assert len(state) > 0


@pytest.mark.asyncio
async def test_create_state_with_metadata(state_manager: StateManager) -> None:
    """Test creating OAuth state with metadata."""
    metadata = {
        "callback_url": "http://localhost:8000/callback",
        "user_id": "user123",
        "provider": "google",
    }

    state = await state_manager.create_state(metadata=metadata)

    # Verify state was created
    assert state is not None

    # Retrieve and verify metadata
    retrieved_metadata = await state_manager.get_state_metadata(state)
    assert retrieved_metadata == metadata


@pytest.mark.asyncio
async def test_create_state_with_custom_ttl(state_manager: StateManager) -> None:
    """Test creating OAuth state with custom TTL."""
    custom_ttl = 1800  # 30 minutes

    state = await state_manager.create_state(ttl=custom_ttl)

    # Verify state was created and stored
    is_valid = await state_manager.validate_state(state)
    assert is_valid is True


@pytest.mark.asyncio
async def test_consume_state(state_manager: StateManager) -> None:
    """Test consuming a valid OAuth state."""
    state = await state_manager.create_state()

    # Consume the state
    state_data = await state_manager.consume_state(state)

    assert isinstance(state_data, StateData)
    assert state_data.state == state
    assert state_data.created_at is not None
    assert state_data.expires_at is not None
    assert state_data.metadata is not None

    # State should no longer be valid after consumption
    is_valid = await state_manager.validate_state(state)
    assert is_valid is False


@pytest.mark.asyncio
async def test_consume_state_with_different_manager(
    memory_backend: MemoryBackend,
) -> None:
    """Test consuming state with a different StateManager instance."""
    BackendProxy.set_backend(memory_backend)

    manager1 = StateManager()
    state = await manager1.create_state()

    # Create a new StateManager instance
    new_manager = StateManager()
    state_data = await new_manager.consume_state(state)

    assert isinstance(state_data, StateData)
    assert state_data.state == state

    # Original manager should also see the state as consumed
    is_valid = await manager1.validate_state(state)
    assert is_valid is False


@pytest.mark.asyncio
async def test_consume_state_with_metadata(state_manager: StateManager) -> None:
    """Test consuming state and retrieving its metadata."""
    metadata = {
        "callback_url": "http://localhost:8000/callback",
        "nonce": "random_nonce",
    }

    state = await state_manager.create_state(metadata=metadata)

    # Consume the state
    state_data = await state_manager.consume_state(state)

    assert state_data.metadata == metadata


@pytest.mark.asyncio
async def test_consume_invalid_state(state_manager: StateManager) -> None:
    """Test consuming an invalid state raises InvalidStateError."""
    with pytest.raises(InvalidStateError, match="Invalid or expired state"):
        await state_manager.consume_state("invalid_state_string")


@pytest.mark.asyncio
async def test_consume_expired_state(state_manager: StateManager) -> None:
    """Test consuming an expired state raises an error.

    Different backends behave differently for expired states:
    - MemoryBackend: InvalidStateError (auto-removes expired entries)
    - RedisBackend: StateExpiredError (TTL expired but data still exists)
    """
    from fastapi_cachex.state.exceptions import StateExpiredError

    # Create state with very short TTL
    state = await state_manager.create_state(ttl=1)

    # Wait for state to expire from cache backend
    import asyncio

    await asyncio.sleep(1.1)

    # Try to consume expired state - should fail with one of these exceptions
    with pytest.raises((InvalidStateError, StateExpiredError)):
        await state_manager.consume_state(state)


@pytest.mark.asyncio
async def test_validate_state(state_manager: StateManager) -> None:
    """Test validating a state without consuming it."""
    state = await state_manager.create_state()

    # Validate the state
    is_valid = await state_manager.validate_state(state)
    assert is_valid is True

    # State should still be valid after validation
    is_valid_again = await state_manager.validate_state(state)
    assert is_valid_again is True


@pytest.mark.asyncio
async def test_validate_invalid_state(state_manager: StateManager) -> None:
    """Test validating an invalid state."""
    is_valid = await state_manager.validate_state("invalid_state")
    assert is_valid is False


@pytest.mark.asyncio
async def test_get_state_metadata_valid(state_manager: StateManager) -> None:
    """Test retrieving metadata from a valid state."""
    metadata = {"key": "value", "nested": {"data": 123}}

    state = await state_manager.create_state(metadata=metadata)

    # Retrieve metadata without consuming
    retrieved = await state_manager.get_state_metadata(state)
    assert retrieved == metadata


@pytest.mark.asyncio
async def test_get_state_metadata_invalid(state_manager: StateManager) -> None:
    """Test retrieving metadata from an invalid state."""
    retrieved = await state_manager.get_state_metadata("invalid_state")
    assert retrieved is None


@pytest.mark.asyncio
async def test_delete_state(state_manager: StateManager) -> None:
    """Test manually deleting a state."""
    state = await state_manager.create_state()

    # Verify state exists
    is_valid = await state_manager.validate_state(state)
    assert is_valid is True

    # Delete the state
    deleted = await state_manager.delete_state(state)
    assert deleted is True

    # Verify state no longer exists
    is_valid_after = await state_manager.validate_state(state)
    assert is_valid_after is False


@pytest.mark.asyncio
async def test_multiple_states_independent(state_manager: StateManager) -> None:
    """Test that multiple states are independent."""
    metadata1 = {"user_id": "user1"}
    metadata2 = {"user_id": "user2"}

    state1 = await state_manager.create_state(metadata=metadata1)
    state2 = await state_manager.create_state(metadata=metadata2)

    # States should be different
    assert state1 != state2

    # Each state should have its own metadata
    retrieved1 = await state_manager.get_state_metadata(state1)
    retrieved2 = await state_manager.get_state_metadata(state2)

    assert retrieved1 == metadata1
    assert retrieved2 == metadata2

    # Consuming one shouldn't affect the other
    await state_manager.consume_state(state1)

    is_valid1 = await state_manager.validate_state(state1)
    is_valid2 = await state_manager.validate_state(state2)

    assert is_valid1 is False
    assert is_valid2 is True


@pytest.mark.asyncio
async def test_state_expiry_information(state_manager: StateManager) -> None:
    """Test that state data contains correct expiry information."""
    ttl = 3600

    state = await state_manager.create_state(ttl=ttl)
    state_data = await state_manager.consume_state(state)

    # Verify timestamps are present and correct
    assert isinstance(state_data.created_at, datetime)
    assert isinstance(state_data.expires_at, datetime)

    # Expiry should be approximately TTL seconds after creation
    time_diff = (state_data.expires_at - state_data.created_at).total_seconds()
    assert abs(time_diff - ttl) < 5  # Allow 5 seconds tolerance


@pytest.mark.asyncio
async def test_state_manager_custom_prefix(memory_backend: MemoryBackend) -> None:
    """Test StateManager with custom key prefix."""
    BackendProxy.set_backend(memory_backend)
    manager = StateManager(key_prefix="custom_prefix:")

    state = await manager.create_state(metadata={"test": "data"})

    # Verify state works with custom prefix
    is_valid = await manager.validate_state(state)
    assert is_valid is True

    # Different manager with different prefix shouldn't find the state
    other_manager = StateManager(key_prefix="other_prefix:")
    is_valid_other = await other_manager.validate_state(state)
    assert is_valid_other is False


@pytest.mark.asyncio
async def test_state_reuse_prevention(state_manager: StateManager) -> None:
    """Test that consumed states cannot be reused."""
    state = await state_manager.create_state()

    # Consume the state once
    await state_manager.consume_state(state)

    # Try to consume again - should fail
    with pytest.raises(InvalidStateError):
        await state_manager.consume_state(state)


@pytest.mark.asyncio
async def test_get_state_metadata_after_expire(state_manager: StateManager) -> None:
    """Test retrieving metadata from an expired state."""
    # Create state with very short TTL
    state = await state_manager.create_state(ttl=1, metadata={"test": "data"})

    # Wait for state to expire
    import asyncio

    await asyncio.sleep(1.1)

    # Try to retrieve metadata - should return None since it's expired
    retrieved = await state_manager.get_state_metadata(state)
    assert retrieved is None


@pytest.mark.asyncio
async def test_consume_state_with_invalid_json(state_manager: StateManager) -> None:
    """Test consuming state when backend returns invalid JSON."""
    # Directly set invalid JSON in backend
    cache_key = f"{state_manager.key_prefix}bad_state"
    etag = hashlib.sha256(b"not valid json").hexdigest()
    etag_content = ETagContent(etag=etag, content="not valid json")
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Try to consume - should raise StateDataError
    with pytest.raises(StateDataError, match="Failed to parse state data"):
        await state_manager.consume_state("bad_state")


@pytest.mark.asyncio
async def test_get_metadata_with_invalid_json(state_manager: StateManager) -> None:
    """Test retrieving metadata when backend returns invalid JSON."""
    # Directly set invalid JSON in backend
    cache_key = f"{state_manager.key_prefix}bad_state"
    etag = hashlib.sha256(b"not valid json").hexdigest()
    etag_content = ETagContent(etag=etag, content="not valid json")
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Try to retrieve metadata - should return None
    retrieved = await state_manager.get_state_metadata("bad_state")
    assert retrieved is None


@pytest.mark.asyncio
async def test_validate_state_with_invalid_json(state_manager: StateManager) -> None:
    """Test validating state when backend returns invalid JSON."""
    # Directly set invalid JSON in backend
    cache_key = f"{state_manager.key_prefix}bad_state"
    etag = hashlib.sha256(b"not valid json").hexdigest()
    etag_content = ETagContent(etag=etag, content="not valid json")
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Try to validate - should return False
    is_valid = await state_manager.validate_state("bad_state")
    assert is_valid is False


@pytest.mark.asyncio
async def test_create_state_empty_metadata(state_manager: StateManager) -> None:
    """Test creating state with empty metadata."""
    state = await state_manager.create_state(metadata={})

    # Verify metadata is empty dict
    metadata = await state_manager.get_state_metadata(state)
    assert metadata == {}


@pytest.mark.asyncio
async def test_state_with_complex_nested_metadata(state_manager: StateManager) -> None:
    """Test state with complex nested metadata structures."""
    complex_metadata = {
        "level1": {
            "level2": {
                "level3": ["item1", "item2", {"key": "value"}],
                "numbers": [1, 2, 3],
            },
            "boolean": True,
            "null_value": None,
        },
        "list_of_dicts": [{"a": 1}, {"b": 2}],
    }

    state = await state_manager.create_state(metadata=complex_metadata)

    retrieved = await state_manager.get_state_metadata(state)
    assert retrieved == complex_metadata


@pytest.mark.asyncio
async def test_get_metadata_with_missing_expiry(state_manager: StateManager) -> None:
    """Test retrieving metadata when state data is missing expiry."""
    state = "test_state"
    cache_key = f"{state_manager.key_prefix}{state}"

    # Create StateData with default expires_at
    state_data_obj = StateData(
        state=state,
        metadata={"test": "data"},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    json_content = json.dumps(state_data_obj.model_dump(mode="json"))
    etag = hashlib.sha256(json_content.encode()).hexdigest()
    etag_content = ETagContent(etag=etag, content=json_content)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Should still work and return metadata
    retrieved = await state_manager.get_state_metadata(state)
    assert retrieved == {"test": "data"}


@pytest.mark.asyncio
async def test_validate_state_with_missing_expiry(state_manager: StateManager) -> None:
    """Test validating state when state data is missing expiry."""
    state = "test_state"
    cache_key = f"{state_manager.key_prefix}{state}"

    state_data_obj = StateData(
        state=state,
        metadata={"test": "data"},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    json_content = json.dumps(state_data_obj.model_dump(mode="json"))
    etag = hashlib.sha256(json_content.encode()).hexdigest()
    etag_content = ETagContent(etag=etag, content=json_content)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Should validate as valid
    is_valid = await state_manager.validate_state(state)
    assert is_valid is True


@pytest.mark.asyncio
async def test_validate_state_with_invalid_expiry_format(
    state_manager: StateManager,
) -> None:
    """Test validating state when expires_at has invalid format."""
    state = "test_state"
    cache_key = f"{state_manager.key_prefix}{state}"

    # Manually construct invalid state data
    state_data = {
        "state": state,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": "invalid-date-format",
        "metadata": {"test": "data"},
    }
    json_content = json.dumps(state_data)
    etag = hashlib.sha256(json_content.encode()).hexdigest()
    etag_content = ETagContent(etag=etag, content=json_content)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Should return False due to invalid expiry
    is_valid = await state_manager.validate_state(state)
    assert is_valid is False


@pytest.mark.asyncio
async def test_get_metadata_with_invalid_expiry_format(
    state_manager: StateManager,
) -> None:
    """Test retrieving metadata when expires_at has invalid format."""
    state = "test_state"
    cache_key = f"{state_manager.key_prefix}{state}"

    # Manually construct invalid state data
    state_data = {
        "state": state,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": "invalid-date-format",
        "metadata": {"test": "data"},
    }
    json_content = json.dumps(state_data)
    etag = hashlib.sha256(json_content.encode()).hexdigest()
    etag_content = ETagContent(etag=etag, content=json_content)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Should return None due to invalid expiry
    retrieved = await state_manager.get_state_metadata(state)
    assert retrieved is None


@pytest.mark.asyncio
async def test_consume_state_with_missing_expiry(state_manager: StateManager) -> None:
    """Test consuming state when state data is missing expiry."""
    state = "test_state"
    cache_key = f"{state_manager.key_prefix}{state}"

    # Manually construct state data without expires_at
    state_data = {
        "state": state,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"test": "data"},
    }
    json_content = json.dumps(state_data)
    etag = hashlib.sha256(json_content.encode()).hexdigest()
    etag_content = ETagContent(etag=etag, content=json_content)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    with pytest.raises(StateDataError):
        await state_manager.consume_state(state)


@pytest.mark.asyncio
async def test_consume_state_with_non_string_content(
    state_manager: StateManager,
) -> None:
    """Test consuming state when backend content is not a string."""
    # Directly set non-string content in backend
    cache_key = f"{state_manager.key_prefix}bad_state"
    # ETagContent with non-string content (testing edge case)
    etag_content = ETagContent(etag="test", content=12345)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Try to consume - should raise StateDataError
    with pytest.raises(StateDataError, match="Unexpected state data format"):
        await state_manager.consume_state("bad_state")


@pytest.mark.asyncio
async def test_validate_state_with_non_string_content(
    state_manager: StateManager,
) -> None:
    """Test validating state when backend content is not a string."""
    cache_key = f"{state_manager.key_prefix}bad_state"
    etag_content = ETagContent(etag="test", content=12345)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Try to validate - should return False
    is_valid = await state_manager.validate_state("bad_state")
    assert is_valid is False


@pytest.mark.asyncio
async def test_get_metadata_with_non_string_content(
    state_manager: StateManager,
) -> None:
    """Test retrieving metadata when backend content is not a string."""
    cache_key = f"{state_manager.key_prefix}bad_state"
    etag_content = ETagContent(etag="test", content=12345)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Try to retrieve metadata - should return None
    retrieved = await state_manager.get_state_metadata("bad_state")
    assert retrieved is None


@pytest.mark.asyncio
async def test_get_metadata_with_non_dict_metadata(state_manager: StateManager) -> None:
    """Test retrieving metadata when metadata is not a dict."""
    state = "test_state"
    cache_key = f"{state_manager.key_prefix}{state}"

    # Manually construct state data with invalid metadata
    state_data = {
        "state": state,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "metadata": "not a dict",  # Invalid metadata type
    }
    json_content = json.dumps(state_data)
    etag = hashlib.sha256(json_content.encode()).hexdigest()
    etag_content = ETagContent(etag=etag, content=json_content)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Should return None since metadata validation fails
    retrieved = await state_manager.get_state_metadata(state)
    assert retrieved is None


@pytest.mark.asyncio
async def test_consume_state_with_bad_expiry_date(state_manager: StateManager) -> None:
    """Test consuming state when expiry date has invalid format."""
    state = "test_state"
    cache_key = f"{state_manager.key_prefix}{state}"

    # Manually construct state data with invalid expiry
    state_data = {
        "state": state,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": "bad-date",
        "metadata": {},
    }
    json_content = json.dumps(state_data)
    etag = hashlib.sha256(json_content.encode()).hexdigest()
    etag_content = ETagContent(etag=etag, content=json_content)
    await state_manager.backend.set(cache_key, etag_content, ttl=600)

    # Should raise StateDataError due to bad expiry format
    with pytest.raises(StateDataError, match="Invalid state data structure"):
        await state_manager.consume_state(state)
