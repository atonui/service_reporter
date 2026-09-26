from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from backend.app.schemas.service_event import StrictModel


class HolidayInput(StrictModel):
    holiday_date: date
    name: str = Field(min_length=1, max_length=160)


class Holiday(HolidayInput):
    id: int
    source: str
    created_at: datetime
    updated_at: datetime
