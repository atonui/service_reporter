import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.schemas.document import ExtractedPage, ParsedDocument
from backend.app.services.extraction import (
    ExtractionError,
    _apply_labelled_document_facts,
    _canonicalize_model_payload,
    _service_type,
    _source_timestamp,
    extract_service_event,
)

FIXTURE = Path(__file__).parent / "fixtures" / "wo_004479870.json"


def source_document() -> ParsedDocument:
    text = """
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
        page_count=1,
        encrypted=False,
        pages_requiring_ocr=[],
        pages=[
            ExtractedPage(
                page_number=1,
                text=text,
                character_count=len(text),
                needs_ocr=False,
            )
        ],
    )


def extraction_json() -> str:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.pop("source_document")
    payload.pop("computed")
    payload["handover"] = {}
    payload["evidence"] = [
        item for item in payload["evidence"] if item["field_path"] != "timing.machine_release"
    ]
    return json.dumps(payload)


def test_falls_back_to_validating_raw_json_when_sdk_has_no_parsed_object(monkeypatch) -> None:
    response = SimpleNamespace(
        output_parsed=None,
        output_text=extraction_json(),
        status="completed",
        incomplete_details=None,
    )

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["max_output_tokens"] == 16_000
            assert kwargs["reasoning"] == {"effort": "low"}
            assert kwargs["text"] == {"format": {"type": "json_object"}}
            return response

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    result = extract_service_event(source_document(), api_key="test-key", model="gpt-5-mini")

    assert result.event.identification.work_order_number == "WO-004479870"
    assert result.event.computed.downtime_hours == 4.5
    assert result.model == "gpt-5-mini"


def test_empty_model_output_has_safe_diagnostic(monkeypatch) -> None:
    response = SimpleNamespace(
        output_parsed=None,
        output_text="",
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    )

    class FakeResponses:
        def create(self, **_kwargs):
            return response

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    with pytest.raises(ExtractionError, match="status=incomplete"):
        extract_service_event(source_document(), api_key="test-key", model="gpt-4o-mini")


def test_accepts_legacy_service_event_envelope(monkeypatch) -> None:
    response = SimpleNamespace(
        output_text=json.dumps({"service_event": json.loads(extraction_json())}),
        status="completed",
        incomplete_details=None,
    )

    class FakeResponses:
        def create(self, **_kwargs):
            return response

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    result = extract_service_event(source_document(), api_key="test-key", model="gpt-4o-mini")

    assert result.event.identification.work_order_number == "WO-004479870"


def test_normalizes_date_only_timing_and_string_activities(monkeypatch) -> None:
    payload = json.loads(extraction_json())
    payload["timing"]["time_in"] = "2026-08-01"
    payload["timing"]["time_out"] = "2026-08-01"
    payload["intervention"]["activities"] = [
        "Investigate/Trouble Shoot",
        "Replace",
        {"action": "Monitor Operation", "duration": "1.0"},
    ]
    response = SimpleNamespace(
        output_text=json.dumps(payload),
        status="completed",
        incomplete_details=None,
    )

    class FakeResponses:
        def create(self, **_kwargs):
            return response

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    result = extract_service_event(source_document(), api_key="test-key", model="gpt-4o-mini")

    assert result.event.timing.time_in is None
    assert result.event.timing.time_out is None
    assert [item.raw_type for item in result.event.intervention.activities] == [
        "Investigate/Trouble Shoot",
        "Replace",
        "Monitor Operation",
    ]
    assert result.event.intervention.activities[2].reported_hours == 1


def test_flattens_structured_address_and_parses_day_first_source_dates() -> None:
    payload = json.loads(extraction_json())
    payload["customer_site"]["address"] = {
        "street": "Sigor Road London",
        "city": "Nakuru Town",
        "postal_code": "20100",
        "country": "KE",
    }
    normalized = _canonicalize_model_payload(payload)

    assert normalized["customer_site"]["address"] == (
        "Sigor Road London, Nakuru Town, 20100, KE"
    )
    assert _source_timestamp("26/02/2026 10:05") == "2026-02-26T10:05:00+03:00"
    assert _source_timestamp("8/27/2026 1:30 PM") == "2026-08-27T13:30:00+03:00"


def test_pmp_and_pmi_are_preventive_and_known_pcsn_supplies_site() -> None:
    assert _service_type("PMP") == "preventive_maintenance"
    assert _service_type("PMI") == "preventive_maintenance"
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["machine"]["pcsn"] = payload["machine"]["asset_id"] = "H194931"
    payload["customer_site"]["site_name"] = "Unknown"
    payload["customer_site"]["customer_name"] = None
    payload["classification"]["service_type"] = "corrective_breakdown"
    event = ServiceEvent.model_validate(payload)
    document = source_document()
    document.pages[0].text += "\nPurpose of Visit: PMI\n"

    corrected = _apply_labelled_document_facts(event, document)

    assert corrected.customer_site.site_name == "Garissa County Referral Hospital"
    assert corrected.classification.service_type.value == "preventive_maintenance"


def test_recovers_labelled_timestamps_and_repair_summary_when_model_omits_them(monkeypatch) -> None:
    payload = json.loads(extraction_json())
    payload["timing"] = {
        "timezone": "Africa/Nairobi",
        "travel_hours": "0.0",
        "site_hours": "0.0",
        "total_work_hours": "4.0",
    }
    payload["intervention"] = {}
    response = SimpleNamespace(
        output_text=json.dumps(payload),
        status="completed",
        incomplete_details=None,
    )

    class FakeResponses:
        def create(self, **_kwargs):
            return response

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    document = source_document()
    document.pages[0].text = """
    Work Order Number WO-004479870
    Agreed Downtime4.00Total Work Hours
    4.00Site Hours0.00Travel Hours
    8/1/2026 12:30 PMMachine Release
    :
    8/1/2026 8:00 AMMalfunction Start :
    Notables
    - Replaced tnuts and motors on leaves A60 and A02.
    - Retuned the beam and cleared the interlock.
    Closure Summary
    MLC InterlocksSubject
    """
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    result = extract_service_event(document, api_key="test-key", model="gpt-4o-mini")

    assert result.event.timing.malfunction_start.isoformat() == "2026-08-01T08:00:00+03:00"
    assert result.event.timing.machine_release.isoformat() == "2026-08-01T12:30:00+03:00"
    assert result.event.timing.total_work_hours == 4
    assert result.event.timing.travel_hours is None
    assert result.event.timing.site_hours is None
    assert "Replaced tnuts" in result.event.intervention.raw_closure_summary
    assert "Retuned the beam" in result.event.intervention.raw_closure_summary
    assert result.event.intervention.normalized_summary == "Component replacement; Beam retuning"
    assert {activity.normalized_type for activity in result.event.intervention.activities} == {
        "component_replacement",
        "beam_retuning",
        "repair",
    }
