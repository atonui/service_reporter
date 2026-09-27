from __future__ import annotations

import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.app.schemas.customer_alias import CustomerAlias, CustomerAliasInput


def normalize_customer_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def canonical_customer_name(value: str, aliases: dict[str, str]) -> str:
    cleaned = value.strip()
    return aliases.get(normalize_customer_key(cleaned), cleaned)


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
        CREATE TABLE IF NOT EXISTS customer_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_name TEXT NOT NULL,
            alias_key TEXT NOT NULL UNIQUE,
            canonical_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return connection


def list_customer_aliases() -> list[CustomerAlias]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM customer_aliases ORDER BY canonical_name COLLATE NOCASE, alias_name COLLATE NOCASE"
        ).fetchall()
    return [_row(row) for row in rows]


def customer_alias_map() -> dict[str, str]:
    return {
        normalize_customer_key(item.alias_name): item.canonical_name
        for item in list_customer_aliases()
    }


def create_customer_alias(value: CustomerAliasInput) -> CustomerAlias:
    alias_name = value.alias_name.strip()
    canonical_name = value.canonical_name.strip()
    alias_key = normalize_customer_key(alias_name)
    if not alias_key:
        raise ValueError("A valid customer alias is required.")
    now = datetime.now(UTC).isoformat()
    try:
        with _connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO customer_aliases (alias_name, alias_key, canonical_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (alias_name, alias_key, canonical_name, now),
            )
            row = connection.execute(
                "SELECT * FROM customer_aliases WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Customer alias {alias_name} already exists.") from exc
    return _row(row)


def delete_customer_alias(alias_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM customer_aliases WHERE id = ?", (alias_id,))
    return cursor.rowcount > 0


def _row(row: sqlite3.Row) -> CustomerAlias:
    return CustomerAlias(
        id=row["id"],
        alias_name=row["alias_name"],
        canonical_name=row["canonical_name"],
        created_at=row["created_at"],
    )
