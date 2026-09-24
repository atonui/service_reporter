from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Hours = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class StrictModel(BaseModel):
    """Reject unknown keys so extraction drift is visible, not silently discarded."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ServiceType(StrEnum):
    PREVENTIVE_MAINTENANCE = "preventive_maintenance"
    CORRECTIVE_BREAKDOWN = "corrective_breakdown"
    REMOTE_SUPPORT = "remote_support"
    CUSTOMER_REQUEST = "customer_request"
    TRAINING = "training"
    OTHER = "other"
    UNKNOWN = "unknown"


class EventStatus(StrEnum):
    OPEN = "open"
    COMPLETE = "complete"
    PARTIALLY_COMPLETE = "partially_complete"
    UNKNOWN = "unknown"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    TEMPORARY_REPAIR = "temporary_repair"
    UNRESOLVED = "unresolved"
    UNKNOWN = "unknown"


class DocumentReference(StrictModel):
    file_name: str
    document_type: Literal["work_order"] = "work_order"
    page_count: int | None = Field(default=None, ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class Identification(StrictModel):
    work_order_number: str = Field(pattern=r"^WO-\d+$")
    case_number: str | None = None
    service_date: datetime | None = None


class CustomerSite(StrictModel):
    customer_name: str | None = None
    site_name: str | None = None
    address: str | None = None
    contact_person: str | None = None


class Machine(StrictModel):
    pcsn: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]+$")
    product_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]+$")
    # Retained for backward compatibility with events extracted before PCSN was formalized.
    asset_id: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None

    @model_validator(mode="before")
    @classmethod
    def synchronize_legacy_asset_id(cls, value):
        if not isinstance(value, dict):
            return value
        result = dict(value)
        if not result.get("pcsn") and result.get("asset_id"):
            result["pcsn"] = result["asset_id"]
        if not result.get("asset_id") and result.get("pcsn"):
            result["asset_id"] = result["pcsn"]
        return result


class ServiceClassification(StrictModel):
    service_type: ServiceType = ServiceType.UNKNOWN
    raw_subject: str | None = None
    fault_category: str | None = None
    fault_subcategory: str | None = None
    fault_codes: list[str] = Field(default_factory=list)
    status: EventStatus = EventStatus.UNKNOWN


class Timing(StrictModel):
    timezone: str = "Africa/Nairobi"
    malfunction_start: datetime | None = None
    time_in: datetime | None = None
    time_out: datetime | None = None
    machine_release: datetime | None = None
    reported_downtime_hours: Hours | None = None
    travel_hours: Hours | None = None
    site_hours: Hours | None = None
    total_work_hours: Hours | None = None

    @model_validator(mode="after")
    def chronological_times(self) -> Timing:
        pairs = (
            ("malfunction_start", self.malfunction_start, "machine_release", self.machine_release),
            ("time_in", self.time_in, "time_out", self.time_out),
        )
        for start_name, start, end_name, end in pairs:
            if start and end and end < start:
                raise ValueError(f"{end_name} cannot be before {start_name}")
        return self


class Diagnosis(StrictModel):
    symptoms: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    diagnostic_steps: list[str] = Field(default_factory=list)
    root_cause: str | None = None


class Activity(StrictModel):
    raw_type: str
    normalized_type: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    reported_hours: Hours | None = None
    description: str | None = None

    @model_validator(mode="after")
    def end_after_start(self) -> Activity:
        if self.start and self.end and self.end < self.start:
            raise ValueError("activity end cannot be before start")
        return self


class Intervention(StrictModel):
    raw_closure_summary: str | None = None
    normalized_summary: str | None = None
    activities: list[Activity] = Field(default_factory=list)
    resolution: str | None = None
    resolution_status: ResolutionStatus = ResolutionStatus.UNKNOWN


class Part(StrictModel):
    part_number: str | None = None
    raw_description: str
    normalized_description: str | None = None
    quantity: Decimal = Field(gt=0)
    source: str | None = None
    to_spares: bool | None = None


class FollowUp(StrictModel):
    description: str
    status: EventStatus = EventStatus.OPEN
    related_work_order_number: str | None = None


class Personnel(StrictModel):
    service_resources: list[str] = Field(default_factory=list)
    customer_signatory: str | None = None


class Handover(StrictModel):
    customer_signed: bool | None = None
    customer_signed_at: datetime | None = None
    engineer_signed: bool | None = None
    engineer_signed_at: datetime | None = None
    quality_statement: str | None = None


class Evidence(StrictModel):
    """One auditable claim supporting one extracted field."""

    field_path: str = Field(
        description="Dot/bracket path, e.g. intervention.activities[0].raw_type"
    )
    page: int = Field(ge=1)
    source_section: str | None = None
    raw_text: str = Field(min_length=1)
    confidence: Confidence
    method: Literal["direct", "normalized", "derived"]


class ComputedMetrics(StrictModel):
    """Deterministic outputs. The extraction model must not populate these."""

    downtime_hours: Hours | None = None
    onsite_elapsed_hours: Hours | None = None
    activity_hours: Hours | None = None


class ExtractedServiceEvent(StrictModel):
    """Model-owned content; excludes trusted document metadata and calculated metrics."""

    schema_version: Literal["0.1"] = "0.1"
    identification: Identification
    customer_site: CustomerSite
    machine: Machine
    classification: ServiceClassification
    timing: Timing
    diagnosis: Diagnosis = Field(default_factory=Diagnosis)
    intervention: Intervention = Field(default_factory=Intervention)
    parts: list[Part] = Field(default_factory=list)
    follow_ups: list[FollowUp] = Field(default_factory=list)
    personnel: Personnel = Field(default_factory=Personnel)
    handover: Handover = Field(default_factory=Handover)
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_paths_are_unique(self) -> ExtractedServiceEvent:
        paths = [item.field_path for item in self.evidence]
        if len(paths) != len(set(paths)):
            raise ValueError("evidence.field_path values must be unique")
        return self


class ServiceEvent(ExtractedServiceEvent):
    source_document: DocumentReference
    computed: ComputedMetrics = Field(default_factory=ComputedMetrics)
