from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import re

from backend.app.schemas.report import (
    MachineAvailability,
    MetricCount,
    PartUsage,
    QuarterlyIncident,
    QuarterlyReport,
    QuarterlyReportRequest,
    ReportFilterOptions,
    RepeatIssue,
    ReportPeriod,
    SiteAvailability,
)
from backend.app.schemas.service_event import ServiceEvent
from backend.app.services.machine_identity import normalize_pcsn, pcsn_details, site_name_for_pcsn

ZERO = Decimal("0.00")
UNPLANNED_TYPES = {"corrective_breakdown"}
PREVENTIVE_PATTERN = re.compile(
    r"\b(?:PMP|PMI|preventive maintenance|planned maintenance)\b", re.IGNORECASE
)


def _q(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _effective_service_type(event: ServiceEvent) -> str:
    """Protect reporting from older events that labelled explicit PMP/PMI work corrective."""
    source_values = [
        event.classification.raw_subject,
        event.classification.fault_category,
        event.classification.fault_subcategory,
        event.intervention.raw_closure_summary,
        event.intervention.normalized_summary,
        *(activity.raw_type for activity in event.intervention.activities),
        *(item.raw_text for item in event.evidence),
    ]
    if any(PREVENTIVE_PATTERN.search(value) for value in source_values if value):
        return "preventive_maintenance"
    return event.classification.service_type.value


def _event_date(event: ServiceEvent) -> datetime | None:
    if event.identification.service_date:
        return event.identification.service_date
    if _effective_service_type(event) == "corrective_breakdown":
        return event.timing.malfunction_start or event.timing.time_in or event.timing.machine_release
    return event.timing.time_in or event.timing.machine_release or event.timing.malfunction_start


def _downtime(event: ServiceEvent) -> Decimal | None:
    if event.timing.reported_downtime_hours is not None:
        return event.timing.reported_downtime_hours
    if _effective_service_type(event) != "corrective_breakdown":
        return None
    if event.computed.downtime_hours is not None:
        return event.computed.downtime_hours
    if event.timing.malfunction_start and event.timing.machine_release:
        hours = (event.timing.machine_release - event.timing.malfunction_start).total_seconds() / 3600
        return _q(str(hours))
    return None


def _pcsn(event: ServiceEvent) -> str | None:
    return normalize_pcsn(event.machine.pcsn or event.machine.asset_id)


def _site_name(event: ServiceEvent) -> str:
    for value in (
        event.customer_site.site_name,
        site_name_for_pcsn(_pcsn(event)),
        event.customer_site.customer_name,
    ):
        if value and value.strip().casefold() not in {"unknown", "unknown site", "n/a"}:
            return value.strip()
    return "Unknown site"


def report_filter_options(events: list[ServiceEvent]) -> ReportFilterOptions:
    """Return canonical filter suggestions while keeping the UI inputs editable."""
    sites = sorted(
        {_site_name(event) for event in events if _site_name(event) != "Unknown site"},
        key=str.casefold,
    )
    pcsns = sorted({pcsn for event in events if (pcsn := _pcsn(event))})
    return ReportFilterOptions(sites=sites, pcsns=pcsns)


def _site_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _period(year: int, quarter: int) -> ReportPeriod:
    first_month = (quarter - 1) * 3 + 1
    last_month = first_month + 2
    return ReportPeriod(label=f"Q{quarter} {year}", start_date=date(year, first_month, 1), end_date=date(year, last_month, monthrange(year, last_month)[1]))


def _metric_rows(values: dict[str, tuple[int, Decimal]]) -> list[MetricCount]:
    return [MetricCount(label=label, count=count, downtime_hours=_q(downtime)) for label, (count, downtime) in sorted(values.items(), key=lambda item: (-item[1][0], item[0]))]


def build_quarterly_report(request: QuarterlyReportRequest) -> QuarterlyReport:
    period = _period(request.year, request.quarter)
    requested_pcsn = normalize_pcsn(request.pcsn)
    requested_site = _site_key(request.site_name) if request.site_name else None
    filtered_events = [
        event
        for event in request.events
        if (not requested_pcsn or _pcsn(event) == requested_pcsn)
        and (not requested_site or _site_key(_site_name(event)) == requested_site)
    ]
    machine_registry: dict[str, ServiceEvent] = {}
    for event in filtered_events:
        pcsn = _pcsn(event)
        if pcsn:
            machine_registry.setdefault(pcsn, event)

    in_period: list[ServiceEvent] = []
    outside_period = 0
    notes: list[str] = []
    for event in filtered_events:
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
    machine_events: dict[str, int] = defaultdict(int)
    machine_corrective_events: dict[str, int] = defaultdict(int)
    machine_downtime: dict[str, Decimal] = defaultdict(lambda: ZERO)
    total_downtime = ZERO
    unplanned_downtime = ZERO
    for event in in_period:
        downtime = _downtime(event)
        downtime_value = downtime or ZERO
        if downtime is None:
            notes.append(f"{event.identification.work_order_number}: downtime is unavailable.")
        else:
            total_downtime += downtime_value
        service_type = _effective_service_type(event)
        unplanned = service_type in UNPLANNED_TYPES
        impact_downtime_value = downtime_value if unplanned else ZERO
        if unplanned:
            unplanned_downtime += downtime_value
        pcsn = _pcsn(event)
        if pcsn:
            machine_events[pcsn] += 1
            if unplanned:
                machine_corrective_events[pcsn] += 1
                machine_downtime[pcsn] += downtime_value
        else:
            notes.append(
                f"{event.identification.work_order_number}: PCSN is unavailable; "
                "the event is not included in a machine availability row."
            )
        def add(
            bucket: dict[str, tuple[int, Decimal]], label: str, hours_to_add: Decimal
        ) -> None:
            count, hours = bucket[label]
            bucket[label] = (count + 1, hours + hours_to_add)
        add(service_types, service_type, impact_downtime_value)
        category = event.classification.fault_category or "Unclassified"
        add(fault_categories, category, impact_downtime_value)
        intervention_group = event.intervention.normalized_summary or event.intervention.raw_closure_summary
        intervention_detail = event.intervention.raw_closure_summary or event.intervention.normalized_summary
        if intervention_group:
            add(interventions, intervention_group, impact_downtime_value)
        issue = event.classification.fault_subcategory or event.classification.fault_category or event.classification.raw_subject
        if issue:
            key = (pcsn, issue)
            count, hours, work_orders = issue_groups.get(key, (0, ZERO, []))
            issue_groups[key] = (count + 1, hours + impact_downtime_value, [*work_orders, event.identification.work_order_number])
        for part in event.parts:
            key = (part.part_number, part.normalized_description or part.raw_description)
            quantity, work_orders = parts.get(key, (Decimal(0), set()))
            parts[key] = (quantity + part.quantity, {*work_orders, event.identification.work_order_number})
        incidents.append(QuarterlyIncident(work_order_number=event.identification.work_order_number, service_date=_event_date(event), pcsn=pcsn, asset_id=pcsn, service_type=service_type, issue=issue, intervention=intervention_detail, downtime_hours=_q(downtime) if downtime is not None else None, included_in_uptime=unplanned and downtime is not None, source_file_name=event.source_document.file_name, evidence_count=len(event.evidence), review_required=not bool(event.evidence)))

    default_basis = request.per_machine_basis()
    overrides = {
        normalize_pcsn(key): Decimal(value)
        for key, value in request.machine_hours_overrides.items()
        if normalize_pcsn(key)
    }
    machine_rows: list[MachineAvailability] = []
    for pcsn, event in sorted(machine_registry.items()):
        basis = overrides.get(pcsn, default_basis)
        downtime = machine_downtime[pcsn]
        machine_uptime = (
            _q(max(ZERO, (basis - downtime) / basis * Decimal(100))) if basis else None
        )
        identity = pcsn_details(pcsn)
        machine_rows.append(
            MachineAvailability(
                pcsn=pcsn,
                product_code=event.machine.product_code or identity["product_code"],
                model=event.machine.model or identity["model"],
                site_name=_site_name(event),
                event_count=machine_events[pcsn],
                corrective_event_count=machine_corrective_events[pcsn],
                unplanned_downtime_hours=_q(downtime),
                working_hours_basis=_q(basis) if basis else None,
                uptime_percent=machine_uptime,
            )
        )

    site_groups: dict[str, list[MachineAvailability]] = defaultdict(list)
    for row in machine_rows:
        site_groups[_site_key(row.site_name)].append(row)
    site_rows: list[SiteAvailability] = []
    for rows in site_groups.values():
        site_basis = sum((row.working_hours_basis or ZERO for row in rows), start=ZERO)
        site_downtime = sum((row.unplanned_downtime_hours for row in rows), start=ZERO)
        site_uptime = (
            _q(max(ZERO, (site_basis - site_downtime) / site_basis * Decimal(100)))
            if site_basis > 0
            else None
        )
        site_rows.append(
            SiteAvailability(
                site_name=rows[0].site_name,
                machine_count=len(rows),
                event_count=sum(row.event_count for row in rows),
                unplanned_downtime_hours=_q(site_downtime),
                working_hours_basis=_q(site_basis) if site_basis > 0 else None,
                uptime_percent=site_uptime,
            )
        )
    site_rows.sort(key=lambda row: row.site_name.casefold())

    total_basis = sum((row.working_hours_basis or ZERO for row in machine_rows), start=ZERO)
    if total_basis > 0:
        if unplanned_downtime > total_basis:
            notes.append("Unplanned downtime exceeds the supplied working-hours basis; uptime set to 0%.")
        uptime = _q(max(ZERO, (total_basis - unplanned_downtime) / total_basis * Decimal(100)))
    else:
        uptime = None
        notes.append("No per-machine working-hours basis supplied; uptime was not calculated.")
    repeat_issues = [RepeatIssue(pcsn=pcsn, asset_id=pcsn, issue=issue, occurrences=count, downtime_hours=_q(hours), work_order_numbers=work_orders) for (pcsn, issue), (count, hours, work_orders) in issue_groups.items() if count >= 2]
    repeat_issues.sort(key=lambda item: (-item.occurrences, -item.downtime_hours, item.issue))
    part_rows = [PartUsage(part_number=number, description=description, quantity=quantity, work_order_numbers=sorted(work_orders)) for (number, description), (quantity, work_orders) in parts.items()]
    part_rows.sort(key=lambda item: (-item.quantity, item.description))
    incidents.sort(key=lambda item: (item.service_date or datetime.min, item.work_order_number))
    return QuarterlyReport(period=period, events_received=len(filtered_events), events_in_period=len(in_period), events_outside_period=outside_period, unplanned_downtime_hours=_q(unplanned_downtime), total_reported_downtime_hours=_q(total_downtime), machine_count=len(machine_rows), working_hours_per_machine=_q(default_basis) if default_basis is not None else None, working_hours_basis=_q(total_basis) if total_basis > 0 else None, uptime_percent=uptime, service_type_breakdown=_metric_rows(service_types), fault_category_breakdown=_metric_rows(fault_categories), intervention_breakdown=_metric_rows(interventions), parts_used=part_rows, repeat_issues=repeat_issues, incidents=incidents, machine_breakdown=machine_rows, site_breakdown=site_rows, review_notes=notes)
