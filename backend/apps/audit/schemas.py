"""HTTP response contracts owned by the audit feature."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    actor: str
    object_type: str
    object_id: str
    case_id: str | None
    action_description: str
    created_at: datetime
    seq: int | None = None
    hash: str | None = None
