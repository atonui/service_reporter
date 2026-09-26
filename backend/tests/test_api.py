from io import BytesIO
import json
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

import backend.app.api.routes as routes
from backend.app.main import app
from backend.app.schemas.extraction import ExtractionResult
from backend.app.schemas.service_event import ServiceEvent
from backend.app.services.event_store import save_extraction

client = TestClient(app)
FIXTURE = Path(__file__).parent / "fixtures" / "wo_004479870.json"


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_application_home_links_the_workflow() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/batch-upload"' in response.text
    assert 'href="/review"' in response.text
    assert 'href="/reports"' in response.text
    assert 'href="/machines"' in response.text


def test_machine_register_page_has_normal_fields_and_crud_endpoints() -> None:
    response = client.get("/machines")
    assert response.status_code == 200
    assert "Customer &amp; Machine Register" in response.text
    assert 'id="customer"' in response.text
    assert 'id="pcsn"' in response.text
    assert 'id="hours"' in response.text
    assert "'/v1/machines'" in response.text


def test_holiday_calendar_page_explains_eight_hour_schedule() -> None:
    response = client.get("/holidays")
    assert response.status_code == 200
    assert "8 operating hours" in response.text
    assert "/v1/holidays" in response.text


def test_quarterly_reports_page_uses_stored_report_endpoints() -> None:
    response = client.get("/reports")
    assert response.status_code == 200
    assert "/v1/reports/quarterly/stored" in response.text
    assert "/v1/reports/quarterly/stored/pdf" in response.text
    assert "Download PDF" in response.text
    assert 'list="site-options"' in response.text
    assert 'list="pcsn-options"' in response.text
    assert "/v1/reports/filter-options" in response.text
    assert "Customer availability" in response.text
    assert "<th>Model</th>" not in response.text
    assert "<th>Product</th>" not in response.text
    assert 'value="custom">Custom dates' in response.text
    assert 'id="start-date"' in response.text
    assert 'id="end-date"' in response.text
    assert 'id="hours"' not in response.text


def test_batch_upload_page_has_a_real_multiple_file_control() -> None:
    response = client.get("/batch-upload")
    assert response.status_code == 200
    assert 'type="file"' in response.text
    assert "multiple" in response.text
    assert "/v1/documents/extract/batch" in response.text


def test_review_page_has_correction_and_approval_controls() -> None:
    response = client.get("/review")
    assert response.status_code == 200
    assert "Save correction" in response.text
    assert "Approve for reports" in response.text
    assert 'id="customer"' in response.text
    assert 'id="pcsn"' in response.text
    assert 'id="service-type"' in response.text
    assert 'id="downtime"' in response.text
    assert 'id="subject"' in response.text
    assert 'id="intervention"' in response.text
    assert 'id="parts-body"' in response.text
    assert "Source evidence" in response.text
    assert 'id="editor"' not in response.text
    assert "Previous value" in response.text
    assert "New value" in response.text
    assert "renderHistory" in response.text
    assert '<pre id="history">' not in response.text


def test_schema_is_exposed() -> None:
    response = client.get("/v1/service-events/schema")
    assert response.status_code == 200
    assert response.json()["title"] == "ServiceEvent"


def test_batch_upload_is_described_as_multiple_binary_files() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/v1/documents/extract/batch"]["post"]
    body_schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    if "$ref" in body_schema:
        body_schema = schema["components"]["schemas"][body_schema["$ref"].rsplit("/", 1)[-1]]
    files_schema = body_schema["properties"]["files"]
    assert files_schema["type"] == "array"
    assert files_schema["items"] == {"type": "string", "format": "binary"}


def test_pdf_parse_endpoint() -> None:
    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(72, 720, "Work Order WO-004479870 with sufficient embedded text")
    document.save()

    response = client.post(
        "/v1/documents/parse",
        files={"file": ("work-order.pdf", buffer.getvalue(), "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page_count"] == 1
    assert "WO-004479870" in payload["pages"][0]["text"]


def test_pdf_parse_endpoint_rejects_wrong_media_type() -> None:
    response = client.post(
        "/v1/documents/parse",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 415


def test_pdf_extract_endpoint(monkeypatch) -> None:
    event = ServiceEvent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))

    def fake_extract(_document):
        return ExtractionResult(
            event=event,
            review_required=False,
            review_flags=[],
            validation_checks=[],
            model="test-model",
        )

    monkeypatch.setattr(routes, "extract_service_event", fake_extract)
    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(72, 720, "Work Order WO-004479870 with sufficient embedded text")
    document.save()

    response = client.post(
        "/v1/documents/extract",
        files={"file": ("work-order.pdf", buffer.getvalue(), "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["event"]["identification"]["work_order_number"] == "WO-004479870"
    assert response.json()["model"] == "test-model"


def test_batch_extract_keeps_successful_files_when_one_file_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "batch-events.db"))
    event = ServiceEvent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))

    def fake_extract(_document):
        return ExtractionResult(
            event=event,
            review_required=False,
            review_flags=[],
            validation_checks=[],
            model="test-model",
        )

    monkeypatch.setattr(routes, "extract_service_event", fake_extract)
    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(72, 720, "Work Order WO-004479870 with sufficient embedded text")
    document.save()

    response = client.post(
        "/v1/documents/extract/batch",
        files=[
            ("files", ("work-order.pdf", buffer.getvalue(), "application/pdf")),
            ("files", ("notes.txt", b"not a pdf", "text/plain")),
        ],
    )

    assert response.status_code == 200
    assert response.json()["files_succeeded"] == 1
    assert response.json()["files_failed"] == 1


def test_quarterly_report_endpoint() -> None:
    event = json.loads(FIXTURE.read_text(encoding="utf-8"))
    response = client.post(
        "/v1/reports/quarterly",
        json={"year": 2026, "quarter": 3, "working_hours_basis": "504", "events": [event]},
    )

    assert response.status_code == 200
    assert response.json()["period"]["label"] == "Q3 2026"
    assert response.json()["unplanned_downtime_hours"] == "4.50"


def test_stored_quarterly_report_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "events.db"))
    event = ServiceEvent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    event.source_document.sha256 = "a" * 64
    save_extraction(
        ExtractionResult(
            event=event,
            review_required=False,
            review_flags=[],
            validation_checks=[],
            model="test-model",
        )
    )

    saved = client.get("/v1/service-events")
    report = client.post(
        "/v1/reports/quarterly/stored",
        json={"year": 2026, "quarter": 3, "working_hours_basis": "504"},
    )
    pdf = client.post(
        "/v1/reports/quarterly/stored/pdf",
        json={"year": 2026, "quarter": 3, "working_hours_basis": "504"},
    )

    assert saved.status_code == 200
    assert len(saved.json()) == 1
    assert saved.json()[0]["review_required"] is False
    assert report.status_code == 200
    assert report.json()["events_in_period"] == 1
    assert report.json()["unplanned_downtime_hours"] == "4.50"
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert "service-report-Q3-2026.pdf" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF")


def test_manual_correction_and_approval_are_audited(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "review-events.db"))
    event = ServiceEvent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    event.source_document.sha256 = "b" * 64
    event_id = save_extraction(
        ExtractionResult(
            event=event,
            review_required=True,
            review_flags=[],
            validation_checks=[],
            model="test-model",
        )
    )
    corrected = event.model_dump(mode="json")
    corrected["classification"]["fault_category"] = "Beam generation"

    saved = client.put(
        f"/v1/service-events/{event_id}",
        json={"event": corrected, "corrected_by": "Allan", "note": "Confirmed from WO"},
    )
    approved = client.post(
        f"/v1/service-events/{event_id}/approve",
        json={"approved_by": "Allan", "note": "Ready for reporting"},
    )

    assert saved.status_code == 200
    assert saved.json()["event"]["classification"]["fault_category"] == "Beam generation"
    assert saved.json()["approval_status"] == "pending_review"
    assert saved.json()["correction_history"][0]["changes"][0]["field_path"] == (
        "classification.fault_category"
    )
    assert approved.status_code == 200
    assert approved.json()["review_required"] is False
    assert approved.json()["approval_status"] == "approved"
    assert approved.json()["approved_by"] == "Allan"
    assert [item["action"] for item in approved.json()["correction_history"]] == [
        "correction",
        "approval",
    ]


def test_manual_correction_cannot_change_source_identity(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SERVICE_INTELLIGENCE_DB_PATH", str(tmp_path / "identity-events.db"))
    event = ServiceEvent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    event.source_document.sha256 = "c" * 64
    event_id = save_extraction(
        ExtractionResult(
            event=event,
            review_required=True,
            review_flags=[],
            validation_checks=[],
            model="test-model",
        )
    )
    changed = event.model_dump(mode="json")
    changed["source_document"]["file_name"] = "different.pdf"

    response = client.put(
        f"/v1/service-events/{event_id}",
        json={"event": changed, "corrected_by": "Allan"},
    )

    assert response.status_code == 422
    assert "source_document.file_name cannot be changed" in response.json()["detail"]
