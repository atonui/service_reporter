from __future__ import annotations

import os
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from backend.app.schemas.holiday import Holiday, HolidayInput
from backend.app.services.business_calendar import standard_kenya_holidays


def _database_path() -> Path:
    configured = os.getenv("SERVICE_INTELLIGENCE_DB_PATH")
    return Path(configured) if configured else Path("data/service_intelligence.db")


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holiday_date TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS holiday_years_seeded (
            year INTEGER PRIMARY KEY,
            seeded_at TEXT NOT NULL
        );
        """
    )
    return connection


def _ensure_year(connection: sqlite3.Connection, year: int) -> None:
    if connection.execute(
        "SELECT 1 FROM holiday_years_seeded WHERE year = ?", (year,)
    ).fetchone():
        return
    now = datetime.now(UTC).isoformat()
    for holiday_date, name in standard_kenya_holidays(year):
        connection.execute(
            """
            INSERT OR IGNORE INTO holidays
                (holiday_date, name, source, created_at, updated_at)
            VALUES (?, ?, 'standard', ?, ?)
            """,
            (holiday_date.isoformat(), name, now, now),
        )
    connection.execute(
        "INSERT INTO holiday_years_seeded (year, seeded_at) VALUES (?, ?)",
        (year, now),
    )


def list_holidays(year: int) -> list[Holiday]:
    with _connect() as connection:
        _ensure_year(connection, year)
        rows = connection.execute(
            "SELECT * FROM holidays WHERE holiday_date LIKE ? ORDER BY holiday_date",
            (f"{year:04d}-%",),
        ).fetchall()
    return [_row(row) for row in rows]


def holiday_dates_for_range(start_date: date, end_date: date) -> list[date]:
    with _connect() as connection:
        for year in range(start_date.year, end_date.year + 1):
            _ensure_year(connection, year)
        rows = connection.execute(
            """SELECT holiday_date FROM holidays
               WHERE holiday_date >= ? AND holiday_date <= ? ORDER BY holiday_date""",
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    return [date.fromisoformat(row["holiday_date"]) for row in rows]


def create_holiday(value: HolidayInput) -> Holiday:
    now = datetime.now(UTC).isoformat()
    try:
        with _connect() as connection:
            cursor = connection.execute(
                """INSERT INTO holidays
                   (holiday_date, name, source, created_at, updated_at)
                   VALUES (?, ?, 'manual', ?, ?)""",
                (value.holiday_date.isoformat(), value.name.strip(), now, now),
            )
            row = connection.execute(
                "SELECT * FROM holidays WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError("A holiday already exists on that date.") from exc
    return _row(row)


def update_holiday(holiday_id: int, value: HolidayInput) -> Holiday | None:
    now = datetime.now(UTC).isoformat()
    try:
        with _connect() as connection:
            cursor = connection.execute(
                """UPDATE holidays SET holiday_date = ?, name = ?, source = 'manual',
                   updated_at = ? WHERE id = ?""",
                (value.holiday_date.isoformat(), value.name.strip(), now, holiday_id),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM holidays WHERE id = ?", (holiday_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError("A holiday already exists on that date.") from exc
    return _row(row)


def delete_holiday(holiday_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM holidays WHERE id = ?", (holiday_id,))
    return cursor.rowcount > 0


def _row(row: sqlite3.Row) -> Holiday:
    return Holiday(
        id=row["id"],
        holiday_date=row["holiday_date"],
        name=row["name"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
