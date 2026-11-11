"""Configuration models for cache backends."""

from pydantic import BaseModel
from pydantic import Field
from pydantic import SecretStr

DEFAULT_KEY_PREFIX = "fastapi_cachex:"
DEFAULT_REDIS_PREFIX = DEFAULT_KEY_PREFIX


class RedisConfig(BaseModel):
    """Configuration for Redis backend."""

    host: str = Field(default="localhost", description="Redis server address")
    port: int = Field(default=6379, ge=1, le=65535, description="Redis server port")
    password: SecretStr | None = Field(
        default=None, description="Redis server password"
    )
    db: int = Field(default=0, ge=0, description="Redis database number")
    username: str | None = Field(
        default=None, description="ACL user name (Redis 6+); None is the default user"
    )
    ssl: bool = Field(default=False, description="Connect over TLS")
    url: SecretStr | None = Field(
        default=None,
        description=(
            "Connection URL (redis://, rediss:// or unix://). It may hold a "
            "password, hence the secret type. When set, host and port are "
            "not used; password, db and username apply only where the URL "
            "does not name its own; ssl must stay False (use rediss://)."
        ),
    )
    socket_timeout: float = Field(
        default=1.0, gt=0, description="Timeout for socket operations in seconds"
    )
    socket_connect_timeout: float = Field(
        default=1.0, gt=0, description="Timeout for socket connection in seconds"
    )
    key_prefix: str = Field(
        default=DEFAULT_REDIS_PREFIX,
        description="Prefix applied to all cache keys",
    )
    protocol: int = Field(
        default=2,
        ge=2,
        le=3,
        description=(
            "RESP protocol version (2 or 3). "
            "Keep at 2 (default) unless you need RESP3 features and your hiredis "
            "version supports it (hiredis >= 3.0 is required for RESP3). "
            "Redis 8.0 supports RESP3 but older hiredis builds do not."
        ),
    )
