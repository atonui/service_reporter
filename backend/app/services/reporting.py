from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from backend.app.schemas.report import MetricCount, PartUsage, QuarterlyIncident, QuarterlyReport, QuarterlyReportRequest, RepeatIssue, ReportPeriod
from backend.app.schemas.service_event import ServiceEvent

ZERO = Decimal("0.00")
UNPLANNED_TYPES = {"corrective_breakdown"}


def _q(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _event_date(event: ServiceEvent) -> datetime | None:
    if event.identification.service_date:
        return event.identification.service_date
    if event.classification.service_type.value == "corrective_breakdown":
        return event.timing.malfunction_start or event.timing.time_in or event.timing.machine_release
    return event.timing.time_in or event.timing.machine_release or event.timing.malfunction_start


def _downtime(event: ServiceEvent) -> Decimal | None:
    if event.timing.reported_downtime_hours is not None:
        return event.timing.reported_downtime_hours
    if event.classification.service_type.value != "corrective_breakdown":
        return None
    if event.computed.downtime_hours is not None:
        return event.computed.downtime_hours
    if event.timing.malfunction_start and event.timing.machine_release:
        hours = (event.timing.machine_release - event.timing.malfunction_start).total_seconds() / 3600
        return _q(str(hours))
    return None


def _period(year: int, quarter: int) -> ReportPeriod:
    first_month = (quarter - 1) * 3 + 1
    last_month = first_month + 2
    return ReportPeriod(label=f"Q{quarter} {year}", start_date=date(year, first_month, 1), end_date=date(year, last_month, monthrange(year, last_month)[1]))


def _metric_rows(values: dict[str, tuple[int, Decimal]]) -> list[MetricCount]:
    return [MetricCount(label=label, count=count, downtime_hours=_q(downtime)) for label, (count, downtime) in sorted(values.items(), key=lambda item: (-item[1][0], item[0]))]


def build_quarterly_report(request: QuarterlyReportRequest) -> QuarterlyReport:
    period = _period(request.year, request.quarter)
    in_period: list[ServiceEvent] = []
    outside_period = 0
    notes: list[str] = []
    for event in request.events:
        event_date = _event_date(event)
        if event_date is None:
            outside_period += 1
            notes.append(f"{event.identification.work_order_number}: no service date; excluded from period.")
        elif period.start_date <= event_date.date() <= period.end_date:
            in_period.append(event)
        else:
            outside_period += 1

    service_types: dict[str, tuple[int, Decimal]] = defaultdict(lambda: (0, ZERO))
    fault_categories: dict[str, tuple[int, Decimal]] = defaultdict(lambda: (0, ZERO))
    interventions: dict[str, tuple[int, Decimal]] = defaultdict(lambda: (0, ZERO))
    parts: dict[tuple[str | None, str], tuple[Decimal, set[str]]] = {}
    issue_groups: dict[tuple[str | None, str], tuple[int, Decimal, list[str]]] = {}
    incidents: list[QuarterlyIncident] = []
    total_downtime = ZERO
    unplanned_downtime = ZERO
    for event in in_period:
        downtime = _downtime(event)
        downtime_value = downtime or ZERO
        if downtime is None:
            notes.append(f"{event.identification.work_order_number}: downtime is unavailable.")
        else:
            total_downtime += downtime_value
        service_type = event.classification.service_type.value
        unplanned = service_type in UNPLANNED_TYPES
        if unplanned:
            unplanned_downtime += downtime_value
        def add(bucket: dict[str, tuple[int, Decimal]], label: str) -> None:
            count, hours = bucket[label]
            bucket[label] = (count + 1, hours + downtime_value)
        add(service_types, service_type)
        category = event.classification.fault_category or "Unclassified"
        add(fault_categories, category)
        intervention_group = event.intervention.normalized_summary or event.intervention.raw_closure_summary
        intervention_detail = event.intervention.raw_closure_summary or event.intervention.normalized_summary
        if intervention_group:
            add(interventions, intervention_group)
        issue = event.classification.fault_subcategory or event.classification.fault_category or event.classification.raw_subject
        if issue:
            key = (event.machine.asset_id, issue)
            count, hours, work_orders = issue_groups.get(key, (0, ZERO, []))
            issue_groups[key] = (count + 1, hours + downtime_value, [*work_orders, event.identification.work_order_number])
        for part in event.parts:
            key = (part.part_number, part.normalized_description or part.raw_description)
            quantity, work_orders = parts.get(key, (Decimal(0), set()))
            parts[key] = (quantity + part.quantity, {*work_orders, event.identification.work_order_number})
        incidents.append(QuarterlyIncident(work_order_number=event.identification.work_order_number, service_date=_event_date(event), asset_id=event.machine.asset_id, service_type=service_type, issue=issue, intervention=intervention_detail, downtime_hours=_q(downtime) if downtime is not None else None, included_in_uptime=unplanned and downtime is not None, source_file_name=event.source_document.file_name, evidence_count=len(event.evidence), review_required=not bool(event.evidence)))

    basis = request.working_hours_basis
    if basis is not None:
        if unplanned_downtime > basis:
            notes.append("Unplanned downtime exceeds the supplied working-hours basis; uptime set to 0%.")
        uptime = _q(max(ZERO, (basis - unplanned_downtime) / basis * Decimal(100)))
    else:
        uptime = None
        notes.append("No working-hours basis supplied; uptime percentage was not calculated.")
    repeat_issues = [RepeatIssue(asset_id=asset_id, issue=issue, occurrences=count, downtime_hours=_q(hours), work_order_numbers=work_orders) for (asset_id, issue), (count, hours, work_orders) in issue_groups.items() if count >= 2]
    repeat_issues.sort(key=lambda item: (-item.occurrences, -item.downtime_hours, item.issue))
    part_rows = [PartUsage(part_number=number, description=description, quantity=quantity, work_order_numbers=sorted(work_orders)) for (number, description), (quantity, work_orders) in parts.items()]
    part_rows.sort(key=lambda item: (-item.quantity, item.description))
    incidents.sort(key=lambda item: (item.service_date or datetime.min, item.work_order_number))
    return QuarterlyReport(period=period, events_received=len(request.events), events_in_period=len(in_period), events_outside_period=outside_period, unplanned_downtime_hours=_q(unplanned_downtime), total_reported_downtime_hours=_q(total_downtime), working_hours_basis=_q(basis) if basis is not None else None, uptime_percent=uptime, service_type_breakdown=_metric_rows(service_types), fault_category_breakdown=_metric_rows(fault_categories), intervention_breakdown=_metric_rows(interventions), parts_used=part_rows, repeat_issues=repeat_issues, incidents=incidents, review_notes=notes)
