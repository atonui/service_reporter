from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from backend.app.schemas.product_catalog import ProductCatalogEntry, ProductCatalogInput
from backend.app.services.machine_identity import PRODUCT_CATALOG


def _database_path() -> Path:
    configured = os.getenv("SERVICE_INTELLIGENCE_DB_PATH")
    return Path(configured) if configured else Path("data/service_intelligence.db")


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'product_catalog'"
    ).fetchone()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS product_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT NOT NULL UNIQUE,
            machine_family TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    if not table_exists:
        now = datetime.now(UTC).isoformat()
        connection.executemany(
            """
            INSERT INTO product_catalog (
                product_code, machine_family, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            [(code, family, now, now) for code, family in PRODUCT_CATALOG.items()],
        )
        connection.commit()
    return connection


def list_product_catalog() -> list[ProductCatalogEntry]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM product_catalog ORDER BY product_code"
        ).fetchall()
    return [_row(row) for row in rows]


def product_catalog_map() -> dict[str, str]:
    return {item.product_code: item.machine_family for item in list_product_catalog()}


def create_product_catalog_entry(value: ProductCatalogInput) -> ProductCatalogEntry:
    code = value.product_code.upper()
    family = value.machine_family.strip()
    now = datetime.now(UTC).isoformat()
    try:
        with _connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO product_catalog (
                    product_code, machine_family, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (code, family, now, now),
            )
            row = connection.execute(
                "SELECT * FROM product_catalog WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Product code {code} already exists.") from exc
    return _row(row)


def update_product_catalog_entry(
    entry_id: int, value: ProductCatalogInput
) -> ProductCatalogEntry | None:
    code = value.product_code.upper()
    family = value.machine_family.strip()
    now = datetime.now(UTC).isoformat()
    try:
        with _connect() as connection:
            cursor = connection.execute(
                """
                UPDATE product_catalog
                SET product_code = ?, machine_family = ?, updated_at = ?
                WHERE id = ?
                """,
                (code, family, now, entry_id),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM product_catalog WHERE id = ?", (entry_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Product code {code} already exists.") from exc
    return _row(row)


def delete_product_catalog_entry(entry_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM product_catalog WHERE id = ?", (entry_id,))
    return cursor.rowcount > 0


def _row(row: sqlite3.Row) -> ProductCatalogEntry:
    return ProductCatalogEntry(
        id=row["id"],
        product_code=row["product_code"],
        machine_family=row["machine_family"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
