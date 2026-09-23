import json
from pathlib import Path

from backend.app.schemas.document import ExtractedPage, ParsedDocument
from backend.app.schemas.service_event import Confidence, ServiceEvent
from backend.app.services.review import review_event

FIXTURE = Path(__file__).parent / "fixtures" / "wo_004479870.json"


def load_event() -> ServiceEvent:
    return ServiceEvent.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def supporting_document() -> ParsedDocument:
    page_one = """
    Work Order Number WO-004479870
    Subject MLC Interlocks
    Malfunction Start: 8/1/2026 8:00 AM
    Machine Release: 8/1/2026 12:30 PM
    Replaced tnuts and motors on leaves A60 and A02. Replaced motor on leaf A29.
    """
    return ParsedDocument(
        file_name="work-order.pdf",
        size_bytes=100,
        sha256="a" * 64,
        page_count=2,
        encrypted=False,
        pages_requiring_ocr=[],
        pages=[
            ExtractedPage(
                page_number=1,
                text=page_one,
                character_count=len(page_one),
                needs_ocr=False,
            ),
            ExtractedPage(
                page_number=2,
                text="Customer and field engineer signatures are present.",
                character_count=51,
                needs_ocr=False,
            ),
        ],
    )


def test_supported_evidence_passes_review() -> None:
    checks, flags = review_event(load_event(), supporting_document())
    assert all(check["passed"] for check in checks)
    assert flags == []


def test_missing_key_evidence_requires_review() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["evidence"] = [
        item
        for item in payload["evidence"]
        if item["field_path"] != "classification.raw_subject"
    ]
    _, flags = review_event(ServiceEvent.model_validate(payload), supporting_document())
    assert any(flag.code == "missing_key_evidence" for flag in flags)


def test_unverifiable_quote_and_low_confidence_are_flagged() -> None:
    event = load_event()
    event.evidence[0].raw_text = "This text is not on the page"
    event.evidence[0].confidence = Confidence.LOW
    _, flags = review_event(event, supporting_document())
    assert {flag.code for flag in flags} >= {"evidence_quote_not_found", "low_confidence"}

