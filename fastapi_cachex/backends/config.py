"""Configuration models for cache backends."""

from pydantic import BaseModel
from pydantic import Field
from pydantic import SecretStr


class RedisConfig(BaseModel):
    """Configuration for Redis backend."""

    host: str = Field(default="localhost", description="Redis server address")
    port: int = Field(default=6379, description="Redis server port")
    password: SecretStr | None = Field(
        default=None, description="Redis server password"
    )
