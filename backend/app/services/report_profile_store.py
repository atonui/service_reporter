from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, time
from decimal import Decimal
from pathlib import Path

from backend.app.schemas.report_profile import ReportProfile, ReportProfileInput


def _database_path() -> Path:
    configured = os.getenv("SERVICE_INTELLIGENCE_DB_PATH")
    return Path(configured) if configured else Path("data/service_intelligence.db")


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'report_profiles'"
    ).fetchone()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS report_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            working_days_json TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            break_minutes INTEGER NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    if not table_exists:
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO report_profiles (
                name, working_days_json, start_time, end_time, break_minutes,
                is_default, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            ("Kenya standard", "[0, 1, 2, 3, 4]", "08:00:00", "17:00:00", 60, now, now),
        )
        connection.commit()
    return connection


def list_report_profiles() -> list[ReportProfile]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM report_profiles ORDER BY is_default DESC, name COLLATE NOCASE"
        ).fetchall()
    return [_row(row) for row in rows]


def get_report_profile(profile_id: int | None = None) -> ReportProfile | None:
    with _connect() as connection:
        if profile_id is None:
            row = connection.execute(
                "SELECT * FROM report_profiles ORDER BY is_default DESC, id LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM report_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
    return _row(row) if row else None


def create_report_profile(value: ReportProfileInput) -> ReportProfile:
    now = datetime.now(UTC).isoformat()
    try:
        with _connect() as connection:
            if value.is_default:
                connection.execute("UPDATE report_profiles SET is_default = 0")
            cursor = connection.execute(
                """
                INSERT INTO report_profiles (
                    name, working_days_json, start_time, end_time, break_minutes,
                    is_default, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value.name.strip(), json.dumps(sorted(value.working_days)),
                    value.start_time.isoformat(), value.end_time.isoformat(),
                    value.break_minutes, int(value.is_default), now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM report_profiles WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Report profile {value.name} already exists.") from exc
    return _row(row)


def update_report_profile(profile_id: int, value: ReportProfileInput) -> ReportProfile | None:
    now = datetime.now(UTC).isoformat()
    try:
        with _connect() as connection:
            if value.is_default:
                connection.execute("UPDATE report_profiles SET is_default = 0")
            cursor = connection.execute(
                """
                UPDATE report_profiles SET
                    name = ?, working_days_json = ?, start_time = ?, end_time = ?,
                    break_minutes = ?, is_default = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    value.name.strip(), json.dumps(sorted(value.working_days)),
                    value.start_time.isoformat(), value.end_time.isoformat(),
                    value.break_minutes, int(value.is_default), now, profile_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM report_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Report profile {value.name} already exists.") from exc
    return _row(row)


def delete_report_profile(profile_id: int) -> bool:
    with _connect() as connection:
        row = connection.execute(
            "SELECT is_default FROM report_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if not row:
            return False
        if row["is_default"]:
            raise ValueError("The default report profile cannot be removed.")
        connection.execute("DELETE FROM report_profiles WHERE id = ?", (profile_id,))
    return True


def _row(row: sqlite3.Row) -> ReportProfile:
    start = time.fromisoformat(row["start_time"])
    end = time.fromisoformat(row["end_time"])
    minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    daily_hours = (Decimal(minutes - row["break_minutes"]) / Decimal(60)).quantize(
        Decimal("0.01")
    )
    return ReportProfile(
        id=row["id"], name=row["name"], working_days=json.loads(row["working_days_json"]),
        start_time=start, end_time=end, break_minutes=row["break_minutes"],
        is_default=bool(row["is_default"]), daily_hours=daily_hours,
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
