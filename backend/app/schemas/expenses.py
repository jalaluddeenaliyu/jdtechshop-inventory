from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.catalog import _blank_to_none, to_kobo

# Suggested categories; the owner may type any other.
COMMON_CATEGORIES = ["Rent", "Electricity & fuel", "Salaries", "Transport", "Internet & phone",
                     "Repairs", "Shop supplies", "Taxes & levies", "Bank charges", "Other"]


class ExpenseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=2, max_length=60)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    spent_on: date | None = None  # defaults to today (shop time)
    method_id: int | None = None
    note: str | None = Field(default=None, max_length=255)

    _k = field_validator("amount")(to_kobo)
    _blank = field_validator("note", mode="before")(_blank_to_none)

    @field_validator("category")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Category must have at least 2 characters.")
        return v


class ExpenseOut(BaseModel):
    id: int
    category: str
    amount: Decimal
    spent_on: date
    method_name: str | None
    note: str | None
    recorded_by_name: str | None
    created_at: datetime
    reversed: bool
    reversal_reason: str | None
