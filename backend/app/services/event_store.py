from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.app.schemas.extraction import ExtractionResult
from backend.app.schemas.service_event import ComputedMetrics, ServiceEvent
from backend.app.schemas.storage import EventApprovalRequest, EventCorrectionRequest
from backend.app.services.validation import derive_metrics, validate_service_event


def _database_path() -> Path:
    configured = os.getenv("SERVICE_INTELLIGENCE_DB_PATH")
    return Path(configured) if configured else Path("data/service_intelligence.db")


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stored_service_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_sha256 TEXT NOT NULL UNIQUE,
            work_order_number TEXT NOT NULL,
            service_date TEXT,
            review_required INTEGER NOT NULL,
            review_flags_json TEXT NOT NULL,
            validation_checks_json TEXT NOT NULL,
            model TEXT NOT NULL,
            event_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    _ensure_review_columns(connection)
    return connection


def _ensure_review_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(stored_service_events)").fetchall()
    }
    additions = {
        "approval_status": "TEXT",
        "approved_by": "TEXT",
        "approved_at": "TEXT",
        "correction_history_json": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE stored_service_events ADD COLUMN {name} {definition}"
            )
    connection.execute(
        """
        UPDATE stored_service_events
        SET approval_status = CASE
            WHEN review_required = 0 THEN 'approved'
            ELSE 'pending_review'
        END
        WHERE approval_status IS NULL OR approval_status = ''
        """
    )


def save_extraction(result: ExtractionResult) -> int:
    """Persist an extraction. Re-extracting the same PDF replaces the prior record."""
    document_sha256 = result.event.source_document.sha256
    if not document_sha256:
        raise ValueError("A source document SHA-256 is required to persist an extraction.")
    now = datetime.now(timezone.utc).isoformat()
    event = result.event.model_dump(mode="json")
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO stored_service_events (
                document_sha256, work_order_number, service_date, review_required,
                review_flags_json, validation_checks_json, model, event_json,
                approval_status, approved_by, approved_at, correction_history_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '[]', ?, ?)
            ON CONFLICT(document_sha256) DO UPDATE SET
                work_order_number=excluded.work_order_number,
                service_date=excluded.service_date,
                review_required=excluded.review_required,
                review_flags_json=excluded.review_flags_json,
                validation_checks_json=excluded.validation_checks_json,
                model=excluded.model,
                event_json=excluded.event_json,
                approval_status=excluded.approval_status,
                approved_by=NULL,
                approved_at=NULL,
                updated_at=excluded.updated_at
            """,
            (
                document_sha256,
                result.event.identification.work_order_number,
                result.event.identification.service_date.isoformat()
                if result.event.identification.service_date
                else None,
                int(result.review_required),
                json.dumps([item.model_dump(mode="json") for item in result.review_flags]),
                json.dumps(result.validation_checks),
                result.model,
                json.dumps(event),
                "pending_review" if result.review_required else "approved",
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT id FROM stored_service_events WHERE document_sha256 = ?", (document_sha256,)
        ).fetchone()
    return int(row["id"])


def list_events(include_review_required: bool = True) -> list[dict]:
    query = "SELECT * FROM stored_service_events"
    parameters: tuple[object, ...] = ()
    if not include_review_required:
        query += " WHERE review_required = 0"
    query += " ORDER BY updated_at DESC, id DESC"
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_record(row) for row in rows]


def get_event(event_id: int) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM stored_service_events WHERE id = ?", (event_id,)
        ).fetchone()
    return _row_to_record(row) if row else None


def correct_event(event_id: int, request: EventCorrectionRequest) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    corrected_event = request.event.model_copy(
        update={"computed": ComputedMetrics(**derive_metrics(request.event))}
    )
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM stored_service_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            return None
        old_event = json.loads(row["event_json"])
        new_event = corrected_event.model_dump(mode="json")
        _assert_source_identity_unchanged(old_event, new_event)
        history = json.loads(row["correction_history_json"] or "[]")
        history.append(
            {
                "action": "correction",
                "actor": request.corrected_by,
                "timestamp": now,
                "note": request.note,
                "changes": _diff_values(old_event, new_event),
            }
        )
        checks = validate_service_event(corrected_event)
        connection.execute(
            """
            UPDATE stored_service_events SET
                work_order_number = ?, service_date = ?, event_json = ?,
                review_required = 1, review_flags_json = '[]',
                validation_checks_json = ?, approval_status = 'pending_review',
                approved_by = NULL, approved_at = NULL,
                correction_history_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                corrected_event.identification.work_order_number,
                corrected_event.identification.service_date.isoformat()
                if corrected_event.identification.service_date
                else None,
                json.dumps(new_event),
                json.dumps(checks),
                json.dumps(history),
                now,
                event_id,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM stored_service_events WHERE id = ?", (event_id,)
        ).fetchone()
    return _row_to_record(updated)


def approve_event(event_id: int, request: EventApprovalRequest) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM stored_service_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            return None
        history = json.loads(row["correction_history_json"] or "[]")
        history.append(
            {
                "action": "approval",
                "actor": request.approved_by,
                "timestamp": now,
                "note": request.note,
                "changes": [],
            }
        )
        connection.execute(
            """
            UPDATE stored_service_events SET
                review_required = 0, review_flags_json = '[]',
                approval_status = 'approved', approved_by = ?, approved_at = ?,
                correction_history_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (request.approved_by, now, json.dumps(history), now, event_id),
        )
        updated = connection.execute(
            "SELECT * FROM stored_service_events WHERE id = ?", (event_id,)
        ).fetchone()
    return _row_to_record(updated)


def _assert_source_identity_unchanged(old_event: dict, new_event: dict) -> None:
    old_source = old_event.get("source_document", {})
    new_source = new_event.get("source_document", {})
    for field in ("sha256", "file_name"):
        if old_source.get(field) != new_source.get(field):
            raise ValueError(f"source_document.{field} cannot be changed during review")


def _diff_values(old: object, new: object, path: str = "") -> list[dict]:
    if isinstance(old, dict) and isinstance(new, dict):
        changes: list[dict] = []
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else key
            changes.extend(_diff_values(old.get(key), new.get(key), child))
        return changes
    if isinstance(old, list) and isinstance(new, list):
        changes = []
        for index in range(max(len(old), len(new))):
            child = f"{path}[{index}]"
            old_value = old[index] if index < len(old) else None
            new_value = new[index] if index < len(new) else None
            changes.extend(_diff_values(old_value, new_value, child))
        return changes
    if old != new:
        return [{"field_path": path, "old_value": old, "new_value": new}]
    return []


def reportable_events() -> list[ServiceEvent]:
    """Only events that passed extraction review are included in automatic reports."""
    return [ServiceEvent.model_validate(item["event"]) for item in list_events(False)]


def _row_to_record(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "event": json.loads(row["event_json"]),
        "review_required": bool(row["review_required"]),
        "review_flags": json.loads(row["review_flags_json"]),
        "validation_checks": json.loads(row["validation_checks_json"]),
        "model": row["model"],
        "approval_status": row["approval_status"],
        "approved_by": row["approved_by"],
        "approved_at": row["approved_at"],
        "correction_history": json.loads(row["correction_history_json"] or "[]"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
