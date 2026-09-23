from __future__ import annotations

from decimal import Decimal

from backend.app.schemas.service_event import ServiceEvent


def _hours_between(start, end) -> Decimal | None:
    if start is None or end is None:
        return None
    return Decimal(str((end - start).total_seconds() / 3600)).quantize(Decimal("0.01"))


def derive_metrics(event: ServiceEvent) -> dict[str, Decimal | None]:
    """Calculate metrics from timestamps; never ask an LLM to calculate them."""
    timing = event.timing
    activity_hours = sum(
        (activity.reported_hours or Decimal(0) for activity in event.intervention.activities),
        start=Decimal(0),
    )
    return {
        "downtime_hours": timing.reported_downtime_hours
        if timing.reported_downtime_hours is not None
        else (
            _hours_between(timing.malfunction_start, timing.machine_release)
            if event.classification.service_type.value == "corrective_breakdown"
            else None
        ),
        "onsite_elapsed_hours": _hours_between(timing.time_in, timing.time_out),
        "activity_hours": activity_hours.quantize(Decimal("0.01")),
    }


def validate_service_event(event: ServiceEvent) -> list[dict[str, str | bool]]:
    metrics = derive_metrics(event)
    checks: list[dict[str, str | bool]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    timing = event.timing
    if timing.total_work_hours is not None and timing.travel_hours is not None and timing.site_hours is not None:
        expected = timing.travel_hours + timing.site_hours
        add(
            "work_hours_reconcile",
            timing.total_work_hours == expected,
            f"total={timing.total_work_hours}; travel+site={expected}",
        )

    if timing.site_hours is not None and metrics["activity_hours"] is not None:
        add(
            "activity_hours_reconcile",
            timing.site_hours == metrics["activity_hours"],
            f"site={timing.site_hours}; line_items={metrics['activity_hours']}",
        )

    if timing.reported_downtime_hours is not None and metrics["downtime_hours"] is not None:
        add(
            "downtime_reconcile",
            timing.reported_downtime_hours == metrics["downtime_hours"],
            f"reported={timing.reported_downtime_hours}; calculated={metrics['downtime_hours']}",
        )

    add(
        "source_traceability",
        bool(event.evidence),
        f"{len(event.evidence)} field-level evidence records",
    )
    return checks
