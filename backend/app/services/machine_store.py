from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.app.schemas.machine_registry import MachineRegistrationInput, RegisteredMachine
from backend.app.services.machine_identity import normalize_pcsn, pcsn_details


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
        CREATE TABLE IF NOT EXISTS registered_machines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pcsn TEXT NOT NULL UNIQUE,
            customer_name TEXT NOT NULL,
            product_code TEXT,
            quarterly_hours TEXT,
            active_from TEXT,
            active_until TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def list_registered_machines() -> list[RegisteredMachine]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM registered_machines ORDER BY customer_name COLLATE NOCASE, pcsn"
        ).fetchall()
    return [_row(row) for row in rows]


def get_registered_machine(machine_id: int) -> RegisteredMachine | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM registered_machines WHERE id = ?", (machine_id,)
        ).fetchone()
    return _row(row) if row else None


def create_registered_machine(value: MachineRegistrationInput) -> RegisteredMachine:
    pcsn = normalize_pcsn(value.pcsn)
    if not pcsn:
        raise ValueError("A valid PCSN is required.")
    now = datetime.now(UTC).isoformat()
    product_code = pcsn_details(pcsn)["product_code"]
    try:
        with _connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO registered_machines (
                    pcsn, customer_name, product_code, quarterly_hours,
                    active_from, active_until, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pcsn,
                    value.customer_name,
                    product_code,
                    str(value.quarterly_hours) if value.quarterly_hours is not None else None,
                    value.active_from.isoformat() if value.active_from else None,
                    value.active_until.isoformat() if value.active_until else None,
                    int(value.active),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM registered_machines WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"PCSN {pcsn} is already registered.") from exc
    return _row(row)


def update_registered_machine(
    machine_id: int, value: MachineRegistrationInput
) -> RegisteredMachine | None:
    pcsn = normalize_pcsn(value.pcsn)
    if not pcsn:
        raise ValueError("A valid PCSN is required.")
    now = datetime.now(UTC).isoformat()
    product_code = pcsn_details(pcsn)["product_code"]
    try:
        with _connect() as connection:
            cursor = connection.execute(
                """
                UPDATE registered_machines SET
                    pcsn = ?, customer_name = ?, product_code = ?, quarterly_hours = ?,
                    active_from = ?, active_until = ?, active = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    pcsn,
                    value.customer_name,
                    product_code,
                    str(value.quarterly_hours) if value.quarterly_hours is not None else None,
                    value.active_from.isoformat() if value.active_from else None,
                    value.active_until.isoformat() if value.active_until else None,
                    int(value.active),
                    now,
                    machine_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM registered_machines WHERE id = ?", (machine_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"PCSN {pcsn} is already registered.") from exc
    return _row(row)


def _row(row: sqlite3.Row) -> RegisteredMachine:
    return RegisteredMachine(
        id=row["id"],
        pcsn=row["pcsn"],
        customer_name=row["customer_name"],
        product_code=row["product_code"],
        quarterly_hours=row["quarterly_hours"],
        active_from=row["active_from"],
        active_until=row["active_until"],
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
