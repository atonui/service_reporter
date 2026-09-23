import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from backend.app.schemas.report import QuarterlyReportRequest
from backend.app.schemas.service_event import ServiceEvent
from backend.app.services.reporting import build_quarterly_report

FIXTURE = Path(__file__).parent / "fixtures" / "wo_004479870.json"


def event_for(date_value: datetime, work_order_number: str) -> ServiceEvent:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["identification"]["work_order_number"] = work_order_number
    payload["identification"]["service_date"] = date_value.isoformat()
    payload["timing"]["malfunction_start"] = date_value.isoformat()
    payload["timing"]["machine_release"] = date_value.replace(hour=12, minute=30).isoformat()
    return ServiceEvent.model_validate(payload)


def test_builds_auditable_quarterly_report() -> None:
    first = event_for(datetime(2026, 8, 1, 8), "WO-004479870")
    second = event_for(datetime(2026, 8, 15, 8), "WO-004479871")
    outside = event_for(datetime(2026, 11, 1, 8), "WO-004479872")

    report = build_quarterly_report(
        QuarterlyReportRequest(
            year=2026,
            quarter=3,
            working_hours_basis="504.00",
            events=[first, second, outside],
        )
    )

    assert report.events_in_period == 2
    assert report.events_outside_period == 1
    assert report.unplanned_downtime_hours == Decimal("9.00")
    assert report.uptime_percent == Decimal("98.21")
    assert report.fault_category_breakdown[0].label == "Interlock"
    assert report.intervention_breakdown[0].label == first.intervention.normalized_summary
    assert report.incidents[0].intervention == first.intervention.raw_closure_summary
    assert report.repeat_issues[0].occurrences == 2
    assert report.incidents[0].source_file_name == "WO-004479870_V1.pdf"


def test_preventive_event_uses_visit_date_and_does_not_infer_downtime() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["identification"]["service_date"] = None
    payload["classification"]["service_type"] = "preventive_maintenance"
    payload["timing"]["malfunction_start"] = "2026-02-26T10:05:00+03:00"
    payload["timing"]["time_in"] = "2026-07-17T14:00:00+03:00"
    payload["timing"]["machine_release"] = "2026-07-18T17:00:00+03:00"
    payload["timing"]["reported_downtime_hours"] = None
    payload["computed"]["downtime_hours"] = None
    event = ServiceEvent.model_validate(payload)

    report = build_quarterly_report(
        QuarterlyReportRequest(year=2026, quarter=3, working_hours_basis="504", events=[event])
    )

    assert report.events_in_period == 1
    assert report.unplanned_downtime_hours == 0
    assert report.total_reported_downtime_hours == 0
    assert report.incidents[0].service_date.isoformat() == "2026-07-17T14:00:00+03:00"
    assert report.incidents[0].downtime_hours is None
