"""HTTP response contracts owned by the audit feature."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AuditEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    actor: str
    object_type: str
    object_id: str
    case_id: Optional[str]
    action_description: str
    created_at: datetime
    seq: Optional[int] = None
    hash: Optional[str] = None
