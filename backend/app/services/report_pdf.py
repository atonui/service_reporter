from __future__ import annotations

from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.app.schemas.report import QuarterlyReport

NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#2878B5")
PALE_BLUE = colors.HexColor("#EAF3F9")
PALE_GREY = colors.HexColor("#F4F6F8")
TEXT = colors.HexColor("#263442")


def _value(value: object | None, fallback: str = "-") -> str:
    return fallback if value is None else str(value)


def render_quarterly_report_pdf(report: QuarterlyReport) -> bytes:
    """Render a management-ready PDF from an already calculated quarterly report."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=18 * mm,
        bottomMargin=17 * mm,
        title=f"Service Intelligence - {report.period.label}",
        author="Service Intelligence",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=4 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Section",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=NAVY,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Cell",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            textColor=TEXT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="HeaderCell",
            parent=styles["Cell"],
            fontName="Helvetica-Bold",
            textColor=colors.white,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Metric",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=NAVY,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            name="MetricLabel",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=7,
            leading=9,
            textColor=TEXT,
            alignment=TA_CENTER,
        )
    )

    def paragraph(value: object | None) -> Paragraph:
        if isinstance(value, Paragraph):
            return value
        return Paragraph(escape(_value(value)), styles["Cell"])

    def section(title: str, data: list[list[object]], widths: list[float]) -> list[object]:
        rows = [
            [Paragraph(escape(_value(cell)), styles["HeaderCell"]) for cell in data[0]],
            *[[paragraph(cell) for cell in row] for row in data[1:]],
        ]
        table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C8D1D9")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_GREY]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return [Paragraph(title, styles["Section"]), table]

    story: list[object] = [
        Paragraph("Quarterly Service Report", styles["ReportTitle"]),
        Paragraph(
            f"{report.period.label} | {report.period.start_date.isoformat()} to "
            f"{report.period.end_date.isoformat()}",
            ParagraphStyle(
                "Period",
                parent=styles["BodyText"],
                alignment=TA_CENTER,
                textColor=BLUE,
                fontSize=9,
                spaceAfter=5 * mm,
            ),
        ),
    ]

    metrics = [
        (report.events_in_period, "Events in period"),
        (report.machine_count, "Machines"),
        (f"{_value(report.working_hours_basis)} h", "Fleet basis"),
        (f"{report.unplanned_downtime_hours} h", "Unplanned downtime"),
        (f"{report.total_reported_downtime_hours} h", "Reported downtime"),
        (f"{report.uptime_percent}%" if report.uptime_percent is not None else "-", "Fleet uptime"),
    ]
    metric_table = Table(
        [
            [Paragraph(str(value), styles["Metric"]) for value, _ in metrics],
            [Paragraph(label, styles["MetricLabel"]) for _, label in metrics],
        ],
        colWidths=[30 * mm] * 6,
    )
    metric_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.7, BLUE),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.white),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
            ]
        )
    )
    story.extend([metric_table, Spacer(1, 2 * mm)])

    if report.machine_breakdown:
        machine_rows: list[list[object]] = [
            ["PCSN", "Product", "Model", "Site", "Events", "Downtime", "Basis", "Uptime"]
        ]
        machine_rows.extend(
            [
                item.pcsn,
                item.product_code or "-",
                item.model or "-",
                item.site_name,
                item.event_count,
                item.unplanned_downtime_hours,
                _value(item.working_hours_basis),
                f"{item.uptime_percent}%" if item.uptime_percent is not None else "-",
            ]
            for item in report.machine_breakdown
        )
        story.extend(
            section(
                "Machine Availability",
                machine_rows,
                [20 * mm, 15 * mm, 27 * mm, 48 * mm, 14 * mm, 19 * mm, 18 * mm, 19 * mm],
            )
        )

    if report.site_breakdown:
        site_rows: list[list[object]] = [
            ["Site", "Machines", "Events", "Downtime (h)", "Basis (h)", "Uptime"]
        ]
        site_rows.extend(
            [
                item.site_name,
                item.machine_count,
                item.event_count,
                item.unplanned_downtime_hours,
                _value(item.working_hours_basis),
                f"{item.uptime_percent}%" if item.uptime_percent is not None else "-",
            ]
            for item in report.site_breakdown
        )
        story.extend(section("Site Availability", site_rows, [75 * mm, 20 * mm, 20 * mm, 25 * mm, 20 * mm, 20 * mm]))

    breakdown_rows: list[list[object]] = [["Service type", "Events", "Downtime (h)"]]
    breakdown_rows.extend(
        [item.label.replace("_", " ").title(), item.count, item.downtime_hours]
        for item in report.service_type_breakdown
    )
    story.extend(section("Service Overview", breakdown_rows, [100 * mm, 35 * mm, 45 * mm]))

    fault_rows: list[list[object]] = [["Fault category", "Events", "Downtime (h)"]]
    fault_rows.extend(
        [item.label, item.count, item.downtime_hours] for item in report.fault_category_breakdown
    )
    if len(fault_rows) > 1:
        story.extend(section("Fault Categories", fault_rows, [120 * mm, 25 * mm, 35 * mm]))

    intervention_rows: list[list[object]] = [["Intervention", "Events", "Downtime (h)"]]
    intervention_rows.extend(
        [item.label, item.count, item.downtime_hours] for item in report.intervention_breakdown
    )
    if len(intervention_rows) > 1:
        story.extend(section("Interventions", intervention_rows, [120 * mm, 25 * mm, 35 * mm]))

    incident_rows: list[list[object]] = [
        ["Work order", "Date", "PCSN", "Issue and intervention", "Downtime", "Uptime impact"]
    ]
    incident_rows.extend(
        [
            item.work_order_number,
            item.service_date.date().isoformat() if item.service_date else "-",
            _value(item.pcsn),
            Paragraph(
                f"<b>{escape(_value(item.issue))}</b><br/>{escape(_value(item.intervention))}",
                styles["Cell"],
            ),
            f"{_value(item.downtime_hours)} h",
            "Yes" if item.included_in_uptime else "No",
        ]
        for item in report.incidents
    )
    story.extend(
        section(
            "Incidents",
            incident_rows,
            [27 * mm, 20 * mm, 19 * mm, 72 * mm, 20 * mm, 22 * mm],
        )
    )

    if report.parts_used:
        part_rows: list[list[object]] = [["Part number", "Description", "Quantity", "Work orders"]]
        part_rows.extend(
            [item.part_number or "-", item.description, item.quantity, ", ".join(item.work_order_numbers)]
            for item in report.parts_used
        )
        story.extend(section("Parts Used", part_rows, [32 * mm, 85 * mm, 23 * mm, 40 * mm]))

    if report.repeat_issues:
        repeat_rows: list[list[object]] = [["PCSN", "Issue", "Occurrences", "Downtime (h)"]]
        repeat_rows.extend(
            [item.pcsn or "-", item.issue, item.occurrences, item.downtime_hours]
            for item in report.repeat_issues
        )
        story.extend(section("Repeat Issues", repeat_rows, [32 * mm, 95 * mm, 25 * mm, 28 * mm]))

    if report.review_notes:
        story.append(Paragraph("Review Notes", styles["Section"]))
        story.extend(Paragraph(f"- {note}", styles["Cell"]) for note in report.review_notes)

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D7DEE5"))
        canvas.line(15 * mm, 12 * mm, 195 * mm, 12 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#647383"))
        canvas.drawString(15 * mm, 8 * mm, "Service Intelligence - auditable work-order reporting")
        canvas.drawRightString(195 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
