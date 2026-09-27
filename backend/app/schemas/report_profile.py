from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from pydantic import Field, model_validator

from backend.app.schemas.service_event import StrictModel


class ReportProfileInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    working_days: list[int] = Field(min_length=1, max_length=7)
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0, le=720)
    is_default: bool = False

    @model_validator(mode="after")
    def valid_schedule(self):
        if len(set(self.working_days)) != len(self.working_days):
            raise ValueError("working_days cannot contain duplicates")
        if any(day < 0 or day > 6 for day in self.working_days):
            raise ValueError("working_days values must be between 0 and 6")
        start_minutes = self.start_time.hour * 60 + self.start_time.minute
        end_minutes = self.end_time.hour * 60 + self.end_time.minute
        if end_minutes <= start_minutes:
            raise ValueError("end_time must be after start_time")
        if self.break_minutes >= end_minutes - start_minutes:
            raise ValueError("break_minutes must be shorter than the working window")
        return self


class ReportProfile(ReportProfileInput):
    id: int
    daily_hours: Decimal = Field(gt=0, decimal_places=2)
    created_at: datetime
    updated_at: datetime

