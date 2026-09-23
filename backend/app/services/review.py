from __future__ import annotations

import re

from backend.app.schemas.document import ParsedDocument
from backend.app.schemas.extraction import ReviewFlag
from backend.app.schemas.service_event import Confidence, ServiceEvent
from backend.app.services.validation import validate_service_event

KEY_EVIDENCE_PATHS = {
    "identification.work_order_number",
    "classification.raw_subject",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def review_event(
    event: ServiceEvent,
    document: ParsedDocument,
) -> tuple[list[dict], list[ReviewFlag]]:
    checks = validate_service_event(event)
    flags: list[ReviewFlag] = []
    page_text = {page.page_number: _normalize(page.text) for page in document.pages}

    for evidence in event.evidence:
        if evidence.page not in page_text:
            flags.append(
                ReviewFlag(
                    severity="error",
                    code="evidence_page_out_of_range",
                    field_path=evidence.field_path,
                    message=f"Evidence points to missing page {evidence.page}.",
                )
            )
        elif _normalize(evidence.raw_text) not in page_text[evidence.page]:
            flags.append(
                ReviewFlag(
                    severity="error",
                    code="evidence_quote_not_found",
                    field_path=evidence.field_path,
                    message="The quoted evidence was not found on the claimed page.",
                )
            )
        if evidence.confidence is Confidence.LOW:
            flags.append(
                ReviewFlag(
                    severity="warning",
                    code="low_confidence",
                    field_path=evidence.field_path,
                    message="The extraction model marked this claim as low confidence.",
                )
            )

    evidence_paths = {item.field_path for item in event.evidence}
    for field_path in sorted(KEY_EVIDENCE_PATHS - evidence_paths):
        flags.append(
            ReviewFlag(
                severity="error",
                code="missing_key_evidence",
                field_path=field_path,
                message="A key extracted field has no supporting evidence.",
            )
        )

    for check in checks:
        if not check["passed"]:
            flags.append(
                ReviewFlag(
                    severity="warning",
                    code="validation_failed",
                    message=f"{check['name']}: {check['detail']}",
                )
            )

    return checks, flags
