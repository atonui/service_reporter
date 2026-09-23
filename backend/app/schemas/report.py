from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from backend.app.schemas.service_event import ServiceEvent, StrictModel


class QuarterlyReportRequest(StrictModel):
    year: int = Field(ge=2020, le=2100)
    quarter: Literal[1, 2, 3, 4]
    events: list[ServiceEvent] = Field(min_length=1)
    working_hours_basis: Decimal | None = Field(default=None, gt=0, decimal_places=2)


class StoredQuarterlyReportRequest(StrictModel):
    year: int = Field(ge=2020, le=2100)
    quarter: Literal[1, 2, 3, 4]
    working_hours_basis: Decimal | None = Field(default=None, gt=0, decimal_places=2)


class ReportPeriod(StrictModel):
    label: str
    start_date: date
    end_date: date


class MetricCount(StrictModel):
    label: str
    count: int = Field(ge=0)
    downtime_hours: Decimal = Field(ge=0, decimal_places=2)


class PartUsage(StrictModel):
    part_number: str | None = None
    description: str
    quantity: Decimal = Field(gt=0)
    work_order_numbers: list[str]


class RepeatIssue(StrictModel):
    asset_id: str | None = None
    issue: str
    occurrences: int = Field(ge=2)
    downtime_hours: Decimal = Field(ge=0, decimal_places=2)
    work_order_numbers: list[str]


class QuarterlyIncident(StrictModel):
    work_order_number: str
    service_date: datetime | None = None
    asset_id: str | None = None
    service_type: str
    issue: str | None = None
    intervention: str | None = None
    downtime_hours: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    included_in_uptime: bool
    source_file_name: str
    evidence_count: int = Field(ge=0)
    review_required: bool


class QuarterlyReport(StrictModel):
    period: ReportPeriod
    events_received: int = Field(ge=0)
    events_in_period: int = Field(ge=0)
    events_outside_period: int = Field(ge=0)
    unplanned_downtime_hours: Decimal = Field(ge=0, decimal_places=2)
    total_reported_downtime_hours: Decimal = Field(ge=0, decimal_places=2)
    working_hours_basis: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    uptime_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    service_type_breakdown: list[MetricCount]
    fault_category_breakdown: list[MetricCount]
    intervention_breakdown: list[MetricCount]
    parts_used: list[PartUsage]
    repeat_issues: list[RepeatIssue]
    incidents: list[QuarterlyIncident]
    review_notes: list[str]

    @model_validator(mode="after")
    def counts_reconcile(self) -> QuarterlyReport:
        if self.events_in_period + self.events_outside_period != self.events_received:
            raise ValueError("event counts do not reconcile")
        return self
