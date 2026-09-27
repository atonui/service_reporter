from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from backend.app.schemas.service_event import StrictModel


class CustomerAliasInput(StrictModel):
    alias_name: str = Field(min_length=1, max_length=240)
    canonical_name: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def names_are_distinct(self):
        if self.alias_name.strip().casefold() == self.canonical_name.strip().casefold():
            raise ValueError("Alias and canonical customer must be different.")
        return self


class CustomerAlias(CustomerAliasInput):
    id: int
    created_at: datetime

