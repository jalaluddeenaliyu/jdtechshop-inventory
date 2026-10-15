from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.catalog import _blank_to_none


def _reason(v: str) -> str:
    v = " ".join(v.split())
    if len(v) < 3:
        raise ValueError("Give a reason (at least 3 characters).")
    return v


class ReasonIn(BaseModel):
    """For reversals: why the record is being undone. Always kept in the history."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(max_length=255)
    # Needed when money goes back to the customer (sale reversal).
    refund_method_id: int | None = None

    _r = field_validator("reason")(_reason)


class SaleReturnLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sale_line_id: int
    quantity: int = Field(default=1, ge=1, le=10_000)
    restock: bool = True  # False: it came back damaged and cannot be sold again


class SaleReturnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[SaleReturnLineIn] = Field(min_length=1, max_length=100)
    reason: str = Field(max_length=255)
    refund_method_id: int | None = None  # how money is given back, if any is due
    idempotency_key: str = Field(min_length=8, max_length=64)

    _r = field_validator("reason")(_reason)


class SupplierReturnLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_line_id: int
    quantity: int = Field(default=1, ge=1, le=100_000)
    # Serial/IMEI items: exactly which units go back (quantity must match).
    unit_ids: list[int] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _units_match(self):
        if self.unit_ids and len(self.unit_ids) != self.quantity:
            raise ValueError("Pick one unit for each item being returned.")
        return self


class SupplierReturnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[SupplierReturnLineIn] = Field(min_length=1, max_length=100)
    reason: str = Field(max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=64)

    _r = field_validator("reason")(_reason)


# ---------- Stock adjustments ----------

AdjustmentKind = Literal["damage", "missing", "adjustment"]
UnitNewStatus = Literal["in_stock", "damaged", "missing"]


class AdjustmentLineIn(BaseModel):
    """Quantity items: `quantity_change` (+ found / − lost). Serial/IMEI items: `unit_id` and
    the unit's new status."""

    model_config = ConfigDict(extra="forbid")

    product_id: int
    quantity_change: int | None = Field(default=None, ge=-100_000, le=100_000)
    kind: AdjustmentKind = "adjustment"
    unit_id: int | None = None
    unit_new_status: UnitNewStatus | None = None

    @model_validator(mode="after")
    def _shape(self):
        if self.unit_id is not None:
            if self.unit_new_status is None:
                raise ValueError("Choose the unit's new status.")
            if self.quantity_change is not None:
                raise ValueError("A unit is adjusted by its status, not a quantity.")
        else:
            if self.unit_new_status is not None:
                raise ValueError("Pick the unit whose status changes.")
            if not self.quantity_change:
                raise ValueError("Enter how many to add (+) or remove (−).")
            if self.kind in ("damage", "missing") and self.quantity_change > 0:
                raise ValueError("Damaged or missing stock is removed: use a negative number.")
        return self


class AdjustmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[AdjustmentLineIn] = Field(min_length=1, max_length=100)
    reason: str = Field(max_length=255)
    approve_now: bool = False  # owner only: apply straight away

    _r = field_validator("reason")(_reason)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=255)

    _b = field_validator("note", mode="before")(_blank_to_none)


class AdjustmentLineOut(BaseModel):
    product_id: int
    product_name: str
    product_sku: str
    unit_id: int | None
    unit_label: str | None
    unit_new_status: str | None
    quantity_change: int
    kind: str


class AdjustmentOut(BaseModel):
    id: int
    status: Literal["pending", "approved", "rejected"]
    reason: str
    requested_by_name: str
    requested_at: datetime
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    lines: list[AdjustmentLineOut]
