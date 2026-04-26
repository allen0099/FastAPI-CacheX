"""Configuration models for cache backends."""

from pydantic import BaseModel
from pydantic import Field
from pydantic import SecretStr

DEFAULT_REDIS_PREFIX = "fastapi_cachex:"


class RedisConfig(BaseModel):
    """Configuration for Redis backend."""

    host: str = Field(default="localhost", description="Redis server address")
    port: int = Field(default=6379, description="Redis server port")
    password: SecretStr | None = Field(
        default=None, description="Redis server password"
    )
    db: int = Field(default=0, description="Redis database number")
    encoding: str = Field(default="utf-8", description="Character encoding to use")
    socket_timeout: float = Field(
        default=1.0, description="Timeout for socket operations in seconds"
    )
    socket_connect_timeout: float = Field(
        default=1.0, description="Timeout for socket connection in seconds"
    )
    key_prefix: str = Field(
        default=DEFAULT_REDIS_PREFIX,
        description="Prefix applied to all cache keys",
    )
