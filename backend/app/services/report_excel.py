from __future__ import annotations

from io import BytesIO
from typing import Any

import xlsxwriter

from backend.app.schemas.report import QuarterlyReport

TEAL = "#009999"
DARK_TEAL = "#006B6B"
ORANGE = "#EC6602"
PALE_TEAL = "#E6F5F5"
PALE_GREY = "#F4F6F8"
TEXT = "#263442"


def render_quarterly_report_excel(report: QuarterlyReport) -> bytes:
    """Render a management-ready Excel workbook from a calculated report."""
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    workbook.set_properties(
        {
            "title": f"Service Intelligence - {report.period.label}",
            "author": "Service Intelligence",
            "comments": "Generated from approved service events.",
        }
    )

    formats = _formats(workbook)
    _summary_sheet(workbook, report, formats)
    _machine_sheet(workbook, report, formats)
    _customer_sheet(workbook, report, formats)
    _incident_sheet(workbook, report, formats)
    _parts_sheet(workbook, report, formats)
    _breakdown_sheet(workbook, report, formats)
    workbook.close()
    return output.getvalue()


def _formats(workbook: xlsxwriter.Workbook) -> dict[str, Any]:
    return {
        "title": workbook.add_format(
            {"font_name": "Arial", "font_size": 16, "bold": True, "font_color": DARK_TEAL}
        ),
        "period": workbook.add_format(
            {"font_name": "Arial", "font_size": 10, "italic": True, "font_color": TEAL}
        ),
        "section": workbook.add_format(
            {"font_name": "Arial", "font_size": 11, "bold": True, "font_color": DARK_TEAL}
        ),
        "header": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 10,
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": DARK_TEAL,
                "align": "center",
                "valign": "vcenter",
                "border": 1,
                "border_color": "#FFFFFF",
            }
        ),
        "body": workbook.add_format(
            {"font_name": "Arial", "font_size": 10, "font_color": TEXT, "valign": "top"}
        ),
        "body_wrap": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 10,
                "font_color": TEXT,
                "valign": "top",
                "text_wrap": True,
            }
        ),
        "hours": workbook.add_format(
            {"font_name": "Arial", "font_size": 10, "font_color": TEXT, "num_format": "0.00"}
        ),
        "percent": workbook.add_format(
            {"font_name": "Arial", "font_size": 10, "font_color": TEXT, "num_format": "0.00\"%\""}
        ),
        "date": workbook.add_format(
            {"font_name": "Arial", "font_size": 10, "font_color": TEXT, "num_format": "yyyy-mm-dd"}
        ),
        "datetime": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 10,
                "font_color": TEXT,
                "num_format": "yyyy-mm-dd hh:mm",
            }
        ),
        "metric_label": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "font_color": TEXT, "bg_color": PALE_TEAL}
        ),
        "metric": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 14,
                "bold": True,
                "font_color": DARK_TEAL,
                "bg_color": PALE_TEAL,
            }
        ),
        "note": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "font_color": TEXT, "italic": True, "text_wrap": True}
        ),
        "orange_rule": workbook.add_format({"bg_color": ORANGE}),
    }


def _prepare_sheet(sheet: Any, title: str, report: QuarterlyReport, formats: dict[str, Any]) -> None:
    sheet.hide_gridlines(2)
    sheet.set_tab_color(TEAL)
    sheet.write("A2", title, formats["title"])
    sheet.write(
        "A3",
        f"{report.period.label} | {report.period.start_date.isoformat()} to "
        f"{report.period.end_date.isoformat()}",
        formats["period"],
    )
    sheet.set_row(3, 3, formats["orange_rule"])


def _write_table(
    sheet: Any,
    start_row: int,
    headers: list[str],
    rows: list[list[object]],
    formats: dict[str, Any],
    *,
    widths: list[int],
    column_formats: dict[int, Any] | None = None,
) -> None:
    column_formats = column_formats or {}
    for column, header in enumerate(headers):
        sheet.write(start_row, column, header, formats["header"])
        sheet.set_column(column, column, widths[column])
    for row_index, row in enumerate(rows, start=start_row + 1):
        if row_index % 2 == 0:
            sheet.set_row(row_index, None, None, {"level": 0})
        for column, value in enumerate(row):
            cell_format = column_formats.get(column, formats["body"])
            if value is None:
                sheet.write_blank(row_index, column, None, cell_format)
            elif hasattr(value, "tzinfo"):
                sheet.write_datetime(row_index, column, value.replace(tzinfo=None), cell_format)
            elif hasattr(value, "year") and hasattr(value, "month"):
                sheet.write_datetime(row_index, column, value, cell_format)
            elif isinstance(value, (int, float)):
                sheet.write_number(row_index, column, value, cell_format)
            else:
                sheet.write(row_index, column, value, cell_format)
    if rows:
        sheet.autofilter(start_row, 0, start_row + len(rows), len(headers) - 1)
        sheet.freeze_panes(start_row + 1, 0)


def _number(value: object | None) -> float | None:
    return None if value is None else float(value)


def _summary_sheet(workbook: Any, report: QuarterlyReport, formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet("Summary")
    _prepare_sheet(sheet, "Service Report", report, formats)
    sheet.set_column("A:A", 30)
    sheet.set_column("B:B", 18)
    metrics = [
        ("Events in period", report.events_in_period),
        ("Machines (PCSNs)", report.machine_count),
        ("Fleet basis (hours)", _number(report.working_hours_basis)),
        ("Unplanned downtime (hours)", _number(report.unplanned_downtime_hours)),
        ("Reported downtime (hours)", _number(report.total_reported_downtime_hours)),
        ("Fleet uptime", None if report.uptime_percent is None else f"{report.uptime_percent}%"),
    ]
    for row, (label, value) in enumerate(metrics, start=5):
        sheet.write(row, 0, label, formats["metric_label"])
        sheet.write(row, 1, value, formats["metric"])
    sheet.write(13, 0, "Service type overview", formats["section"])
    rows = [[item.label.replace("_", " ").title(), item.count, _number(item.downtime_hours)] for item in report.service_type_breakdown]
    _write_table(sheet, 14, ["Service type", "Events", "Downtime (h)"], rows, formats, widths=[34, 12, 18], column_formats={2: formats["hours"]})
    note_row = 17 + len(rows)
    sheet.write(note_row, 0, "Review notes", formats["section"])
    notes = report.review_notes or ["No review notes."]
    for index, note in enumerate(notes, start=note_row + 1):
        sheet.merge_range(index, 0, index, 3, f"• {note}", formats["note"])


def _machine_sheet(workbook: Any, report: QuarterlyReport, formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet("Machine Availability")
    _prepare_sheet(sheet, "Machine Availability", report, formats)
    rows = [
        [item.pcsn, item.site_name, item.event_count, item.corrective_event_count, _number(item.unplanned_downtime_hours), _number(item.working_hours_basis), _number(item.uptime_percent)]
        for item in report.machine_breakdown
    ]
    _write_table(sheet, 5, ["PCSN", "Customer", "Events", "Corrective", "Downtime (h)", "Basis (h)", "Uptime (%)"], rows, formats, widths=[16, 42, 11, 12, 16, 15, 14], column_formats={4: formats["hours"], 5: formats["hours"], 6: formats["percent"]})


def _customer_sheet(workbook: Any, report: QuarterlyReport, formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet("Customer Availability")
    _prepare_sheet(sheet, "Customer Availability", report, formats)
    rows = [[item.site_name, item.machine_count, item.event_count, _number(item.unplanned_downtime_hours), _number(item.working_hours_basis), _number(item.uptime_percent)] for item in report.site_breakdown]
    _write_table(sheet, 5, ["Customer", "Machines", "Events", "Downtime (h)", "Basis (h)", "Uptime (%)"], rows, formats, widths=[46, 12, 11, 16, 15, 14], column_formats={3: formats["hours"], 4: formats["hours"], 5: formats["percent"]})


def _incident_sheet(workbook: Any, report: QuarterlyReport, formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet("Incidents")
    _prepare_sheet(sheet, "Incident Detail", report, formats)
    rows = [[item.work_order_number, item.service_date, item.pcsn, item.service_type.replace("_", " ").title(), item.issue, item.intervention, _number(item.downtime_hours), "Yes" if item.included_in_uptime else "No", item.source_file_name, item.evidence_count, "Yes" if item.review_required else "No"] for item in report.incidents]
    _write_table(sheet, 5, ["Work order", "Service date", "PCSN", "Service type", "Issue", "Intervention", "Downtime (h)", "Uptime impact", "Source file", "Evidence", "Review required"], rows, formats, widths=[20, 20, 14, 22, 38, 50, 16, 16, 34, 12, 18], column_formats={1: formats["datetime"], 4: formats["body_wrap"], 5: formats["body_wrap"], 6: formats["hours"]})


def _parts_sheet(workbook: Any, report: QuarterlyReport, formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet("Parts")
    _prepare_sheet(sheet, "Parts Used", report, formats)
    rows = [[item.part_number, item.description, _number(item.quantity), ", ".join(item.work_order_numbers)] for item in report.parts_used]
    _write_table(sheet, 5, ["Part number", "Description", "Quantity", "Work orders"], rows, formats, widths=[20, 50, 12, 42], column_formats={1: formats["body_wrap"], 2: formats["hours"], 3: formats["body_wrap"]})


def _breakdown_sheet(workbook: Any, report: QuarterlyReport, formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet("Breakdowns")
    _prepare_sheet(sheet, "Breakdowns and Repeat Issues", report, formats)
    row = 5
    sections = [
        ("Fault categories", ["Category", "Events", "Downtime (h)"], [[item.label, item.count, _number(item.downtime_hours)] for item in report.fault_category_breakdown], [40, 12, 18]),
        ("Interventions", ["Intervention", "Events", "Downtime (h)"], [[item.label, item.count, _number(item.downtime_hours)] for item in report.intervention_breakdown], [58, 12, 18]),
        ("Repeat issues", ["PCSN", "Issue", "Occurrences", "Downtime (h)", "Work orders"], [[item.pcsn, item.issue, item.occurrences, _number(item.downtime_hours), ", ".join(item.work_order_numbers)] for item in report.repeat_issues], [16, 42, 14, 18, 40]),
    ]
    for title, headers, rows, widths in sections:
        sheet.write(row, 0, title, formats["section"])
        _write_table(sheet, row + 1, headers, rows, formats, widths=widths, column_formats={2 if len(headers) == 3 else 3: formats["hours"]})
        row += len(rows) + 5
