from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from backend.app.schemas.document import ParsedDocument
from backend.app.schemas.extraction import ExtractionResult
from backend.app.schemas.service_event import (
    ComputedMetrics,
    DocumentReference,
    ExtractedServiceEvent,
    ServiceEvent,
)
from backend.app.services.review import review_event
from backend.app.services.validation import derive_metrics
from backend.app.services.machine_identity import pcsn_details, site_name_for_pcsn

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_MAX_OUTPUT_TOKENS = 16_000
DEFAULT_REASONING_EFFORT = "low"
ALLOWED_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}

SYSTEM_PROMPT = """You extract field-service work orders into a JSON object matching the service-event schema.
Use only facts supported by the supplied document text.
Preserve source wording in raw fields and place interpretations only in normalized fields.
Use null, empty lists, or unknown when information is absent.
Never infer root cause from symptoms, repairs, or installed parts.
Never calculate durations or totals; extract timestamps and explicitly reported values only.
For each important claim, include the smallest supporting quote, page number, and confidence.
Do not treat a signature as proof that the technical problem was resolved.
The document timezone is Africa/Nairobi unless the source explicitly states otherwise.
Keep the response compact: include at most 12 evidence records, only for material non-null
claims; keep each source quote under 240 characters; and do not repeat document text in
summaries. Prefer null or an empty list to speculative detail.
Return only one valid JSON object. Omit fields that are absent rather than inventing values.
Do not include source_document or computed; the application supplies those trusted fields.
machine.pcsn is the globally unique top-level machine identifier labelled Asset on these work
orders. Do not confuse it with a subcomponent. PCSNs contain letters and numbers only. Product-code
length varies: known prefixes are H19=TrueBeam Platform, HAL=Halcyon, and H29=Clinac. Preserve an
unknown full PCSN without guessing where its product code ends.
Put event fields directly at the top level. Do not wrap them in `service_event`, `event`,
`data`, or any other enclosing key.
Use these top-level objects exactly: identification, customer_site, machine, classification,
timing, diagnosis, intervention, parts, follow_ups, personnel, handover, evidence.
"""


class ExtractionConfigurationError(RuntimeError):
    pass


class ExtractionError(RuntimeError):
    pass


def _document_text(document: ParsedDocument) -> str:
    sections = [
        f"FILE: {document.file_name}",
        f"SHA256: {document.sha256}",
        f"PAGE COUNT: {document.page_count}",
    ]
    for page in document.pages:
        sections.append(f"\n--- PAGE {page.page_number} ---\n{page.text}")
    return "\n".join(sections)


def _max_output_tokens() -> int:
    value = os.getenv("OPENAI_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS))
    try:
        budget = int(value)
    except ValueError as exc:
        raise ExtractionConfigurationError(
            "OPENAI_MAX_OUTPUT_TOKENS must be a whole number."
        ) from exc
    if not 1_024 <= budget <= 32_768:
        raise ExtractionConfigurationError(
            "OPENAI_MAX_OUTPUT_TOKENS must be between 1024 and 32768."
        )
    return budget


def _reasoning_effort() -> str:
    effort = os.getenv("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).lower()
    if effort not in ALLOWED_REASONING_EFFORTS:
        allowed = ", ".join(sorted(ALLOWED_REASONING_EFFORTS))
        raise ExtractionConfigurationError(
            f"OPENAI_REASONING_EFFORT must be one of: {allowed}."
        )
    return effort


def _safe_response_diagnostic(response: Any) -> str:
    """Return operational metadata without exposing document content in an API error."""
    status = getattr(response, "status", "unknown")
    incomplete = getattr(response, "incomplete_details", None)
    reason = getattr(incomplete, "reason", None) if incomplete else None
    text_present = bool(getattr(response, "output_text", ""))
    usage = getattr(response, "usage", None)
    output_tokens = getattr(usage, "output_tokens", None) if usage else None
    output_details = getattr(usage, "output_tokens_details", None) if usage else None
    reasoning_tokens = getattr(output_details, "reasoning_tokens", None) if output_details else None
    return (
        f"status={status}; incomplete_reason={reason or 'none'}; "
        f"output_text_present={text_present}; output_tokens={output_tokens}; "
        f"reasoning_tokens={reasoning_tokens}"
    )


def _safe_request_diagnostic(exc: Exception) -> str:
    """Return API failure metadata without exposing the API key or document text."""
    status_code = getattr(exc, "status_code", None)
    message = str(exc).replace("\n", " ").strip()
    if len(message) > 500:
        message = f"{message[:497]}..."
    return (
        f"status_code={status_code if status_code is not None else 'unknown'}; "
        f"error_type={type(exc).__name__}; message={message or 'unavailable'}"
    )


def _safe_validation_diagnostic(exc: ValidationError) -> str:
    """Expose invalid field paths and rule types, never the model/PDF values themselves."""
    issues = []
    for error in exc.errors()[:12]:
        location = ".".join(str(part) for part in error.get("loc", ("root",)))
        issues.append(f"{location}:{error.get('type', 'validation_error')}")
    suffix = "; ..." if len(exc.errors()) > 12 else ""
    return "; ".join(issues) + suffix


def _service_type(value: Any) -> str:
    """Map common work-order labels to the canonical service-type vocabulary."""
    if not isinstance(value, str):
        return "unknown"
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    if "prevent" in normalized or normalized in {"pm", "pmp", "pmi"}:
        return "preventive_maintenance"
    if "correct" in normalized or "breakdown" in normalized or "repair" in normalized:
        return "corrective_breakdown"
    if "remote" in normalized:
        return "remote_support"
    if "customer" in normalized or "request" in normalized:
        return "customer_request"
    if "training" in normalized:
        return "training"
    return normalized if normalized in {"other", "unknown"} else "unknown"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([value] if value else [])


def _text_or_none(value: Any) -> str | None:
    """Flatten common model objects/lists into a compact human-readable text value."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        parts = [_text_or_none(item) for item in value.values()]
    elif isinstance(value, list):
        parts = [_text_or_none(item) for item in value]
    else:
        return None
    unique: list[str] = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return ", ".join(unique) or None


def _decimal_or_none(value: Any) -> Any:
    """Keep numeric hours; turn source placeholders such as 'N/A' into null."""
    if value is None or isinstance(value, (int, float, Decimal)):
        return value
    if isinstance(value, str):
        try:
            return str(Decimal(value.strip()))
        except (InvalidOperation, ValueError):
            return None
    return None


def _datetime_or_none(value: Any) -> str | None:
    """Normalize model timestamps; reject date-only guesses for fields requiring a time."""
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}", text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    for pattern in (
        "%m/%d/%Y %I:%M %p",
        "%d/%m/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%d/%m/%Y %H:%M",
    ):
        try:
            parsed = datetime.strptime(re.sub(r"\s+", " ", text), pattern)
            return parsed.replace(tzinfo=timezone(timedelta(hours=3))).isoformat()
        except ValueError:
            continue
    return None


def _event_status(value: Any, *, default: str = "unknown") -> str:
    if not isinstance(value, str):
        return default
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    aliases = {"completed": "complete", "closed": "complete", "in_progress": "partially_complete"}
    return aliases.get(normalized, normalized) if normalized in {
        "open", "complete", "completed", "closed", "partially_complete", "in_progress", "unknown"
    } else default


def _resolution_status(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    aliases = {"complete": "resolved", "completed": "resolved", "closed": "resolved"}
    return aliases.get(normalized, normalized) if normalized in {
        "resolved", "temporary_repair", "unresolved", "complete", "completed", "closed", "unknown"
    } else "unknown"


def _canonicalize_model_payload(payload: Any) -> Any:
    """Accept a common flat work-order draft while persisting the canonical nested shape."""
    if not isinstance(payload, dict):
        return payload
    if isinstance(payload.get("service_event"), dict):
        payload = payload["service_event"]
    if "identification" in payload:
        return _canonicalize_nested_payload(payload)

    work_order = payload.get("work_order_number")
    if not isinstance(work_order, str):
        return payload

    nested = {
        "schema_version": payload.get("schema_version", "0.1"),
        "identification": {
            "work_order_number": work_order,
            "case_number": payload.get("case_number"),
            "service_date": payload.get("service_date") or payload.get("timestamp"),
        },
        "customer_site": {
            "customer_name": payload.get("customer_name"),
            "site_name": payload.get("site_name") or payload.get("customer_site"),
            "address": payload.get("customer_address") or payload.get("address"),
            "contact_person": payload.get("contact_person"),
        },
        "machine": {
            "pcsn": payload.get("pcsn") or payload.get("asset_id") or payload.get("machine_id"),
            "product_code": payload.get("product_code"),
            "asset_id": payload.get("asset_id") or payload.get("pcsn") or payload.get("machine_id"),
            "manufacturer": payload.get("manufacturer"),
            "model": payload.get("model") or payload.get("machine_model"),
            "serial_number": payload.get("serial_number"),
        },
        "classification": {
            "service_type": _service_type(payload.get("event_type") or payload.get("service_type")),
            "raw_subject": payload.get("subject") or payload.get("raw_subject"),
            "fault_category": payload.get("fault_category"),
            "fault_subcategory": payload.get("fault_subcategory"),
            "fault_codes": _as_list(payload.get("fault_codes")),
            "status": _event_status(payload.get("status")),
        },
        "timing": {
            "timezone": payload.get("timezone", "Africa/Nairobi"),
            "malfunction_start": payload.get("malfunction_start"),
            "time_in": payload.get("time_in"),
            "time_out": payload.get("time_out"),
            "machine_release": payload.get("machine_release"),
            "reported_downtime_hours": payload.get("reported_downtime_hours"),
            "travel_hours": payload.get("travel_hours"),
            "site_hours": payload.get("site_hours"),
            "total_work_hours": payload.get("total_work_hours"),
        },
        "diagnosis": {
            "symptoms": _as_list(payload.get("symptoms")),
            "observations": _as_list(payload.get("observations")),
            "diagnostic_steps": _as_list(payload.get("diagnostic_steps")),
            "root_cause": payload.get("root_cause"),
        },
        "intervention": {
            "raw_closure_summary": payload.get("closure_summary") or payload.get("action_taken"),
            "normalized_summary": payload.get("intervention_summary"),
            "activities": payload.get("activities", []),
            "resolution": payload.get("resolution"),
            "resolution_status": _resolution_status(payload.get("resolution_status")),
        },
        "parts": payload.get("parts", []),
        "follow_ups": payload.get("follow_ups", []),
        "personnel": {
            "service_resources": _as_list(payload.get("service_resources") or payload.get("engineers")),
            "customer_signatory": payload.get("customer_signatory"),
        },
        "handover": {
            "customer_signed": payload.get("customer_signed"),
            "customer_signed_at": payload.get("customer_signed_at"),
            "engineer_signed": payload.get("engineer_signed"),
            "engineer_signed_at": payload.get("engineer_signed_at"),
            "quality_statement": payload.get("quality_statement"),
        },
        "evidence": payload.get("evidence", []),
    }
    return _canonicalize_nested_payload(nested)


def _canonicalize_nested_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize recurring model aliases within an otherwise nested event response."""
    result = dict(payload)

    def object_with_aliases(name: str, aliases: dict[str, str], allowed: set[str]) -> dict[str, Any]:
        value = result.get(name)
        if not isinstance(value, dict):
            return {}
        normalized = {aliases.get(key, key): item for key, item in value.items()}
        return {key: item for key, item in normalized.items() if key in allowed}

    result["identification"] = object_with_aliases(
        "identification",
        {"customer_reference": "case_number", "date": "service_date"},
        {"work_order_number", "case_number", "service_date"},
    )
    result["customer_site"] = object_with_aliases(
        "customer_site",
        {"name": "site_name", "customer": "customer_name", "location": "address", "contact": "contact_person"},
        {"customer_name", "site_name", "address", "contact_person"},
    )
    for text_field in {"customer_name", "site_name", "address", "contact_person"}:
        result["customer_site"][text_field] = _text_or_none(
            result["customer_site"].get(text_field)
        )
    result["machine"] = object_with_aliases(
        "machine",
        {"type": "model", "machine_type": "model", "id": "pcsn", "asset": "pcsn", "serial": "serial_number"},
        {"pcsn", "product_code", "asset_id", "manufacturer", "model", "serial_number"},
    )
    result["classification"] = object_with_aliases(
        "classification",
        {"activity_type": "service_type", "malfunction_subject": "raw_subject", "subject": "raw_subject"},
        {"service_type", "raw_subject", "fault_category", "fault_subcategory", "fault_codes", "status"},
    )
    classification = result["classification"]
    classification["service_type"] = _service_type(classification.get("service_type"))
    classification["status"] = _event_status(classification.get("status"))

    result["timing"] = object_with_aliases(
        "timing",
        {"agreed_downtime": "reported_downtime_hours", "downtime_hours": "reported_downtime_hours"},
        {"timezone", "malfunction_start", "time_in", "time_out", "machine_release", "reported_downtime_hours", "travel_hours", "site_hours", "total_work_hours"},
    )
    for hour_field in {"reported_downtime_hours", "travel_hours", "site_hours", "total_work_hours"}:
        result["timing"][hour_field] = _decimal_or_none(result["timing"].get(hour_field))
    for datetime_field in {
        "malfunction_start",
        "time_in",
        "time_out",
        "machine_release",
    }:
        result["timing"][datetime_field] = _datetime_or_none(
            result["timing"].get(datetime_field)
        )

    diagnosis = result.get("diagnosis") if isinstance(result.get("diagnosis"), dict) else {}
    observations = _as_list(diagnosis.get("observations"))
    observations.extend(_as_list(diagnosis.get("summary")))
    observations.extend(_as_list(diagnosis.get("findings")))
    result["diagnosis"] = {
        "symptoms": _as_list(diagnosis.get("symptoms")),
        "observations": observations,
        "diagnostic_steps": _as_list(diagnosis.get("diagnostic_steps")),
        "root_cause": diagnosis.get("root_cause"),
    }

    intervention = result.get("intervention") if isinstance(result.get("intervention"), dict) else {}
    actions = _as_list(intervention.get("actions"))
    normalized_activities = []
    for activity in _as_list(intervention.get("activities")):
        if isinstance(activity, str) and activity.strip():
            normalized_activities.append({"raw_type": activity.strip()})
            continue
        if not isinstance(activity, dict):
            continue
        raw_type = (
            activity.get("raw_type")
            or activity.get("activity_type")
            or activity.get("action")
            or activity.get("type")
            or activity.get("description")
        )
        if not isinstance(raw_type, str) or not raw_type.strip():
            continue
        normalized_activities.append(
            {
                "raw_type": raw_type.strip(),
                "normalized_type": activity.get("normalized_type"),
                "start": _datetime_or_none(activity.get("start")),
                "end": _datetime_or_none(activity.get("end")),
                "reported_hours": _decimal_or_none(
                    activity.get("reported_hours")
                    if "reported_hours" in activity
                    else activity.get("duration")
                ),
                "description": activity.get("description"),
            }
        )
    result["intervention"] = {
        "raw_closure_summary": intervention.get("raw_closure_summary") or intervention.get("summary") or ("; ".join(str(item) for item in actions) if actions else None),
        "normalized_summary": intervention.get("normalized_summary"),
        "activities": normalized_activities,
        "resolution": intervention.get("resolution"),
        "resolution_status": _resolution_status(intervention.get("resolution_status")),
    }

    normalized_follow_ups = []
    for follow_up in _as_list(result.get("follow_ups") or result.get("follow_up")):
        if isinstance(follow_up, str) and follow_up.strip():
            normalized_follow_ups.append({"description": follow_up.strip(), "status": "open"})
        elif isinstance(follow_up, dict):
            description = follow_up.get("description") or follow_up.get("action") or follow_up.get("summary")
            if isinstance(description, str) and description.strip():
                normalized_follow_ups.append(
                    {
                        "description": description.strip(),
                        "status": _event_status(follow_up.get("status"), default="open"),
                        "related_work_order_number": follow_up.get("related_work_order_number"),
                    }
                )

    normalized_parts = []
    for part in _as_list(result.get("parts")):
        if not isinstance(part, dict):
            continue
        description = part.get("raw_description") or part.get("part_name") or part.get("description")
        quantity = _decimal_or_none(part.get("quantity") if "quantity" in part else part.get("installed_qty"))
        if description is None or quantity is None:
            continue
        normalized_parts.append(
            {
                "part_number": part.get("part_number") or part.get("part_no"),
                "raw_description": description,
                "normalized_description": part.get("normalized_description"),
                "quantity": quantity,
                "source": part.get("source") or part.get("source_of_parts"),
                "to_spares": part.get("to_spares"),
            }
        )
    result["parts"] = normalized_parts
    result["follow_ups"] = normalized_follow_ups
    result["personnel"] = object_with_aliases("personnel", {"engineers": "service_resources"}, {"service_resources", "customer_signatory"})
    result["personnel"]["service_resources"] = _as_list(result["personnel"].get("service_resources"))
    result["handover"] = object_with_aliases("handover", {}, {"customer_signed", "customer_signed_at", "engineer_signed", "engineer_signed_at", "quality_statement"})
    normalized_evidence = []
    for index, item in enumerate(_as_list(result.get("evidence"))):
        if not isinstance(item, dict):
            continue
        raw_text = item.get("raw_text") or item.get("supporting_quote") or item.get("quote")
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue
        confidence = str(item.get("confidence", "medium")).lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        method = str(item.get("method", "direct")).lower()
        if method not in {"direct", "normalized", "derived"}:
            method = "direct"
        page = item.get("page") or item.get("page_number") or 1
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        normalized_evidence.append(
            {
                "field_path": item.get("field_path") or item.get("field") or f"model_output.evidence[{index}]",
                "page": page,
                "source_section": item.get("source_section") or item.get("section"),
                "raw_text": raw_text.strip(),
                "confidence": confidence,
                "method": method,
            }
        )
    result["evidence"] = normalized_evidence
    result["schema_version"] = result.get("schema_version", "0.1")
    return result


def _parse_model_output(response: Any) -> ExtractedServiceEvent:
    """Validate the raw JSON so incomplete Responses are handled before parsing."""
    if getattr(response, "status", None) != "completed":
        detail = _safe_response_diagnostic(response)
        raise ExtractionError(f"The model did not complete extraction ({detail}).")

    output_text = getattr(response, "output_text", "")
    if output_text:
        try:
            payload = json.loads(output_text)
            payload = _canonicalize_model_payload(payload)
            return ExtractedServiceEvent.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            detail = _safe_response_diagnostic(response)
            validation = (
                _safe_validation_diagnostic(exc)
                if isinstance(exc, ValidationError)
                else "root:json_invalid"
            )
            raise ExtractionError(
                "The model returned JSON that does not match the extraction schema "
                f"({detail}; validation={validation})."
            ) from exc

    detail = _safe_response_diagnostic(response)
    raise ExtractionError(f"The model returned no structured extraction ({detail}).")


def _labelled_document_facts(document: ParsedDocument) -> list[tuple[str, str, str, int]]:
    """Read unambiguous labelled fields from the PDF text without an LLM interpretation."""
    facts: list[tuple[str, str, str, int]] = []
    for page in document.pages:
        text = page.text
        patterns = {
            "identification.work_order_number": r"(?P<value>WO-\d+)\s*Work\s*Order",
            "identification.case_number": r"(?P<value>\d{6,})\s*Case\s*WO-\d+",
            "machine.pcsn": r"(?P<value>[A-Z]{1,4}\d{3,})\s*Asset\b",
            "classification.raw_subject": r"(?P<value>[^\n]{1,200}?)\s*Subject\b",
        }
        for field_path, pattern in patterns.items():
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                facts.append((field_path, match.group("value"), match.group(0), page.page_number))
        hours = re.search(
            r"Agreed Downtime\s*(?P<downtime>\d+(?:\.\d+)?)\s*Total Work Hours\s*"
            r"(?P<total>\d+(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if hours:
            fields = {
                "timing.reported_downtime_hours": "downtime",
                "timing.total_work_hours": "total",
            }
            for field_path, group in fields.items():
                facts.append((field_path, hours.group(group), hours.group(0), page.page_number))
        facts.extend(_timestamp_facts(text, page.page_number))
    return facts


_TIMESTAMP = r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}(?:\s*(?:AM|PM))?"
_TIMESTAMP_FIELDS = {
    "Malfunction Start": "timing.malfunction_start",
    "Time In": "timing.time_in",
    "Time Out": "timing.time_out",
    "Machine Release": "timing.machine_release",
}


def _source_timestamp(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", value.strip())
    parsed = None
    for pattern in (
        "%m/%d/%Y %I:%M %p",
        "%d/%m/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%d/%m/%Y %H:%M",
    ):
        try:
            parsed = datetime.strptime(normalized, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    # Kenya is UTC+03:00 and does not observe daylight saving time. A fixed offset keeps
    # extraction portable to Windows Python installations that lack the IANA tz database.
    return parsed.replace(tzinfo=timezone(timedelta(hours=3))).isoformat()


def _timestamp_facts(text: str, page_number: int) -> list[tuple[str, str, str, int]]:
    """Read PDF timestamps whether extraction placed the label before or after its value."""
    facts: list[tuple[str, str, str, int]] = []
    for label, field_path in _TIMESTAMP_FIELDS.items():
        # This order matters for flattened work orders such as ``12:00 PMTime Out8/27...``.
        patterns = (
            rf"(?P<value>{_TIMESTAMP})\s*{re.escape(label)}\s*:?\s*",
            rf"{re.escape(label)}\s*:\s*(?P<value>{_TIMESTAMP})",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                parsed = _source_timestamp(match.group("value"))
                if parsed:
                    facts.append((field_path, parsed, match.group(0), page_number))
                break
    return facts


def _deterministic_intervention(text: str) -> tuple[str, str] | None:
    """Recover repair actions from the labelled Notables block when the model omits them."""
    block = re.search(r"Notables\s*(?P<value>.*?)(?:Closure Summary|Work Order Times|Parts Installed)", text, re.IGNORECASE | re.DOTALL)
    if not block:
        return None
    sentences = re.split(r"(?<=[.!?])\s+|\n\s*[-•]\s*", block.group("value"))
    repairs = [
        sentence.strip(" -\n")
        for sentence in sentences
        if re.search(r"\b(replaced|retuned|repaired|cleared|resolved|restored)\b", sentence, re.IGNORECASE)
    ]
    if not repairs:
        return None
    summary = "; ".join(repairs)
    return summary[:1_000], block.group(0)[:240]


_INTERVENTION_RULES = (
    ("diagnostic_testing", "Diagnostic testing", r"\b(checked|tested|inspected|disconnected|tried beaming|troubleshoot)\b"),
    ("pcb_replacement", "PCB replacement", r"\b(replaced|changed)\b[^.;\n]{0,100}\b(PCB|board)\b"),
    ("thyratron_replacement", "Thyratron replacement", r"\b(replaced|changed)\b[^.;\n]{0,100}\bthyratron\b"),
    ("beam_retuning", "Beam retuning", r"\b(retuned|retune|tuned)\b[^.;\n]{0,100}\b(beam|6X|BGM)\b"),
    ("operational_monitoring", "Operational monitoring", r"\b(monitored|monitor operation|observed operation)\b"),
    ("component_replacement", "Component replacement", r"\b(replaced|changed)\b"),
    ("repair", "Repair", r"\b(repaired|restored|resolved|cleared the issue)\b"),
)


def _normalized_intervention(raw_summary: str) -> tuple[str, list[dict[str, str]]]:
    """Create stable report categories while keeping source wording in each activity."""
    sentences = [
        item.strip(" -")
        for item in re.split(r";\s*|(?<=[.!?])\s+|\n\s*[-•]?\s*", raw_summary)
        if item.strip(" -")
    ]
    activities: list[dict[str, str]] = []
    labels: list[str] = []
    matched_types: set[str] = set()
    for normalized_type, label, pattern in _INTERVENTION_RULES:
        for sentence in sentences:
            if normalized_type in matched_types or not re.search(pattern, sentence, re.IGNORECASE):
                continue
            # A specific replacement category is more useful than the generic fallback.
            if normalized_type == "component_replacement" and {
                "pcb_replacement",
                "thyratron_replacement",
            } & matched_types:
                continue
            activities.append({"raw_type": sentence, "normalized_type": normalized_type})
            labels.append(label)
            matched_types.add(normalized_type)
            break

    primary = [
        label
        for normalized_type, label, _ in _INTERVENTION_RULES
        if normalized_type in matched_types
        and normalized_type not in {"diagnostic_testing", "operational_monitoring", "repair"}
    ]
    if not primary:
        primary = labels
    return "; ".join(primary), activities


def _apply_labelled_document_facts(event: ServiceEvent, document: ParsedDocument) -> ServiceEvent:
    """Correct model output only when a clearly labelled source value is present."""
    payload = event.model_dump(mode="python")
    evidence_paths = {item["field_path"] for item in payload["evidence"]}
    for field_path, value, quote, page in _labelled_document_facts(document):
        section, field = field_path.split(".", 1)
        payload[section][field] = value
        if field_path not in evidence_paths:
            source_section = (
                "Work Order Information"
                if section in {"identification", "machine"}
                else "Work Order Comments"
                if section == "classification"
                else "Work Order Times"
            )
            payload["evidence"].append(
                {
                    "field_path": field_path,
                    "page": page,
                    "source_section": source_section,
                    "raw_text": quote[:240],
                    "confidence": "high",
                    "method": "direct",
                }
            )
            evidence_paths.add(field_path)

    timing = payload["timing"]

    machine = payload["machine"]
    identity = pcsn_details(machine.get("pcsn") or machine.get("asset_id"))
    if identity["pcsn"]:
        machine["pcsn"] = identity["pcsn"]
        machine["asset_id"] = identity["pcsn"]
    for field in ("product_code", "serial_number", "model"):
        if not machine.get(field) and identity[field]:
            machine[field] = identity[field]

    customer_site = payload["customer_site"]
    known_site = site_name_for_pcsn(identity["pcsn"])
    current_site = customer_site.get("site_name")
    if known_site and (
        not current_site or str(current_site).strip().casefold() in {"unknown", "unknown site", "n/a"}
    ):
        customer_site["site_name"] = known_site

    document_text = "\n".join(page.text for page in document.pages)
    preventive_match = re.search(
        r"\b(?:PMP|PMI|preventive maintenance|planned maintenance)\b",
        document_text,
        flags=re.IGNORECASE,
    )
    if preventive_match:
        payload["classification"]["service_type"] = "preventive_maintenance"
        field_path = "classification.service_type"
        if field_path not in evidence_paths:
            source_page = next(
                page.page_number
                for page in document.pages
                if preventive_match.group(0).casefold() in page.text.casefold()
            )
            payload["evidence"].append(
                {
                    "field_path": field_path,
                    "page": source_page,
                    "source_section": "Work Order Information",
                    "raw_text": preventive_match.group(0),
                    "confidence": "high",
                    "method": "normalized",
                }
            )
            evidence_paths.add(field_path)
    total_work_hours = timing.get("total_work_hours")
    travel_hours = timing.get("travel_hours")
    site_hours = timing.get("site_hours")
    component_paths = {"timing.travel_hours", "timing.site_hours"}
    if (
        total_work_hours is not None
        and travel_hours is not None
        and site_hours is not None
        and Decimal(str(total_work_hours)) > 0
        and Decimal(str(travel_hours)) + Decimal(str(site_hours)) == 0
        and "timing.total_work_hours" in evidence_paths
        and not (component_paths & evidence_paths)
    ):
        # Flattened PDF columns can make the model attach a visible zero to both
        # component fields. Keep the directly labelled total, but do not persist
        # unsupported component guesses that fail reconciliation.
        timing["travel_hours"] = None
        timing["site_hours"] = None

    if not payload["intervention"].get("raw_closure_summary"):
        for page in document.pages:
            intervention = _deterministic_intervention(page.text)
            if not intervention:
                continue
            summary, quote = intervention
            payload["intervention"]["raw_closure_summary"] = summary
            field_path = "intervention.raw_closure_summary"
            if field_path not in evidence_paths:
                payload["evidence"].append(
                    {
                        "field_path": field_path,
                        "page": page.page_number,
                        "source_section": "Work Order Comments",
                        "raw_text": quote,
                        "confidence": "high",
                        "method": "direct",
                    }
                )
                evidence_paths.add(field_path)
            break

    raw_intervention = payload["intervention"].get("raw_closure_summary")
    if raw_intervention and not payload["intervention"].get("normalized_summary"):
        normalized_summary, activities = _normalized_intervention(raw_intervention)
        if normalized_summary:
            payload["intervention"]["normalized_summary"] = normalized_summary
        if activities and not payload["intervention"].get("activities"):
            payload["intervention"]["activities"] = activities
        field_path = "intervention.normalized_summary"
        raw_evidence = next(
            (
                item
                for item in payload["evidence"]
                if item["field_path"] == "intervention.raw_closure_summary"
            ),
            None,
        )
        if normalized_summary and raw_evidence and field_path not in evidence_paths:
            payload["evidence"].append(
                {
                    "field_path": field_path,
                    "page": raw_evidence["page"],
                    "source_section": raw_evidence["source_section"],
                    "raw_text": raw_evidence["raw_text"],
                    "confidence": "high",
                    "method": "normalized",
                }
            )
            evidence_paths.add(field_path)

    reported_downtime = payload["timing"].get("reported_downtime_hours")
    if (
        payload["classification"].get("service_type") == "unknown"
        and reported_downtime is not None
        and Decimal(str(reported_downtime)) > 0
    ):
        payload["classification"]["service_type"] = "corrective_breakdown"
        if "classification.service_type" not in evidence_paths:
            downtime_evidence = next(
                (item for item in payload["evidence"] if item["field_path"] == "timing.reported_downtime_hours"),
                None,
            )
            payload["evidence"].append(
                {
                    "field_path": "classification.service_type",
                    "page": downtime_evidence["page"] if downtime_evidence else 1,
                    "source_section": "Work Order Times",
                    "raw_text": downtime_evidence["raw_text"] if downtime_evidence else "Positive agreed downtime recorded.",
                    "confidence": "medium",
                    "method": "derived",
                }
            )
            evidence_paths.add("classification.service_type")

    if not payload["parts"]:
        for page in document.pages:
            for match in re.finditer(
                r"(?m)^(?P<number>\d{6,})\s+(?P<description>.+?)\s+(?P<quantity>\d+(?:\.\d+)?)\s+(?P<source>[A-Z]\s*-\s*[^\n]+)$",
                page.text,
            ):
                payload["parts"].append(
                    {
                        "part_number": match.group("number"),
                        "raw_description": match.group("description").strip(),
                        "quantity": match.group("quantity"),
                        "source": match.group("source").strip(),
                    }
                )
    return ServiceEvent.model_validate(payload)


def extract_service_event(
    document: ParsedDocument,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> ExtractionResult:
    if document.pages_requiring_ocr:
        pages = ", ".join(str(page) for page in document.pages_requiring_ocr)
        raise ExtractionError(f"OCR is required before extraction for page(s): {pages}.")

    resolved_key = api_key or os.getenv("OPENAI_API_KEY")
    resolved_model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    output_budget = _max_output_tokens()
    reasoning_effort = _reasoning_effort()
    if not resolved_key:
        raise ExtractionConfigurationError("OPENAI_API_KEY is not configured.")

    try:
        from openai import OpenAI

        client = OpenAI(api_key=resolved_key)
        request: dict[str, Any] = {
            "model": resolved_model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _document_text(document)},
            ],
            # Avoid SDK `responses.parse`: it tries to validate an empty body before the
            # caller can inspect an incomplete response. JSON mode lets Pydantic validate
            # only the fields the model actually returns, so sparse work orders stay small.
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": output_budget,
            "store": False,
        }
        # The reasoning option is not accepted by non-reasoning models such as gpt-4o-mini.
        if resolved_model.startswith("gpt-5"):
            request["reasoning"] = {"effort": reasoning_effort}

        response = client.responses.create(
            **request,
        )
    except Exception as exc:
        detail = _safe_request_diagnostic(exc)
        raise ExtractionError(f"The model extraction request failed ({detail}).") from exc

    extracted = _parse_model_output(response)

    event = ServiceEvent(
        **extracted.model_dump(),
        source_document=DocumentReference(
            file_name=document.file_name,
            page_count=document.page_count,
            sha256=document.sha256,
        ),
    )
    event = _apply_labelled_document_facts(event, document)
    event = event.model_copy(
        update={"computed": ComputedMetrics(**derive_metrics(event))},
        deep=True,
    )
    checks, flags = review_event(event, document)
    return ExtractionResult(
        event=event,
        review_required=any(flag.severity in {"warning", "error"} for flag in flags),
        review_flags=flags,
        validation_checks=checks,
        model=resolved_model,
    )
