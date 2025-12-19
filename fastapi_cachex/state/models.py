"""Data models for state management."""

from datetime import datetime
from datetime import timezone
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_serializer


class StateData(BaseModel):
    """OAuth state data model."""

    model_config = ConfigDict()

    state: str = Field(..., description="The unique state identifier")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the state was created",
    )
    expires_at: datetime = Field(..., description="When the state expires")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata associated with state"
    )

    @field_serializer("created_at", "expires_at", when_used="json")
    def serialize_datetime(self, value: datetime) -> str:
        """Serialize datetime to ISO format string for JSON."""
        return value.isoformat()
