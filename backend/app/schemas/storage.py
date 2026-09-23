from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from backend.app.schemas.extraction import ReviewFlag
from backend.app.schemas.service_event import ServiceEvent, StrictModel


class FieldChange(StrictModel):
    field_path: str
    old_value: Any = None
    new_value: Any = None


class CorrectionHistoryEntry(StrictModel):
    action: Literal["correction", "approval"]
    actor: str
    timestamp: datetime
    note: str | None = None
    changes: list[FieldChange] = Field(default_factory=list)


class StoredServiceEvent(StrictModel):
    id: int
    event: ServiceEvent
    review_required: bool
    review_flags: list[ReviewFlag]
    validation_checks: list[dict[str, str | bool]]
    model: str
    approval_status: Literal["pending_review", "approved"]
    approved_by: str | None = None
    approved_at: datetime | None = None
    correction_history: list[CorrectionHistoryEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EventCorrectionRequest(StrictModel):
    event: ServiceEvent
    corrected_by: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class EventApprovalRequest(StrictModel):
    approved_by: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)
