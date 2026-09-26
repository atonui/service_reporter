from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, model_validator

from backend.app.schemas.service_event import StrictModel


class MachineRegistrationInput(StrictModel):
    customer_name: str = Field(min_length=1, max_length=240)
    pcsn: str = Field(pattern=r"^[A-Za-z0-9]+$", max_length=40)
    quarterly_hours: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    active_from: date | None = None
    active_until: date | None = None
    active: bool = True

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.active_from and self.active_until and self.active_until < self.active_from:
            raise ValueError("active_until cannot be before active_from")
        return self


class RegisteredMachine(MachineRegistrationInput):
    id: int
    product_code: str | None = None
    created_at: datetime
    updated_at: datetime
