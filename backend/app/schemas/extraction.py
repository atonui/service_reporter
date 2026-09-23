from typing import Literal

from backend.app.schemas.service_event import ServiceEvent, StrictModel


class ReviewFlag(StrictModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    field_path: str | None = None


class ExtractionResult(StrictModel):
    event: ServiceEvent
    review_required: bool
    review_flags: list[ReviewFlag]
    validation_checks: list[dict[str, str | bool]]
    model: str


class BatchExtractionItem(StrictModel):
    file_name: str
    status: Literal["succeeded", "failed"]
    extraction: ExtractionResult | None = None
    error: str | None = None


class BatchExtractionResult(StrictModel):
    files_received: int
    files_succeeded: int
    files_failed: int
    items: list[BatchExtractionItem]
