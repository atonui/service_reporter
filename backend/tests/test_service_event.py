import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.schemas.service_event import ServiceEvent
from backend.app.services.validation import derive_metrics, validate_service_event

FIXTURE = Path(__file__).parent / "fixtures" / "wo_004479870.json"


def load_event() -> ServiceEvent:
    return ServiceEvent.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_real_work_order_fixture_validates() -> None:
    event = load_event()
    assert event.identification.work_order_number == "WO-004479870"
    assert event.diagnosis.root_cause is None
    assert len(event.parts) == 3


def test_metrics_are_deterministic() -> None:
    metrics = derive_metrics(load_event())
    assert metrics == {
        "downtime_hours": Decimal("4.50"),
        "onsite_elapsed_hours": Decimal("2.50"),
        "activity_hours": Decimal("2.50"),
    }


def test_hours_reconcile() -> None:
    checks = validate_service_event(load_event())
    assert checks
    assert all(check["passed"] for check in checks)


def test_unknown_fields_are_rejected() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["hallucinated_root_cause"] = "motor failure"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ServiceEvent.model_validate(payload)


def test_negative_part_quantity_is_rejected() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["parts"][0]["quantity"] = -1
    with pytest.raises(ValidationError):
        ServiceEvent.model_validate(payload)

