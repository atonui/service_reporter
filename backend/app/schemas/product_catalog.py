from __future__ import annotations

from datetime import datetime

from pydantic import Field

from backend.app.schemas.service_event import StrictModel


class ProductCatalogInput(StrictModel):
    product_code: str = Field(pattern=r"^[A-Za-z0-9]+$", max_length=20)
    machine_family: str = Field(min_length=1, max_length=160)


class ProductCatalogEntry(ProductCatalogInput):
    id: int
    created_at: datetime
    updated_at: datetime

