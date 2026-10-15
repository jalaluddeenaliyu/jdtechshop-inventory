from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.catalog import _blank_to_none


class OpeningStockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int = Field(ge=1, le=1_000_000)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    note: str | None = Field(default=None, max_length=255)

    _blank = field_validator("note", mode="before")(_blank_to_none)

    @field_validator("unit_cost")
    @classmethod
    def _four_places(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.0001"))


class StockMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    product_name: str | None = None
    product_sku: str | None = None
    unit_id: int | None = None
    unit_label: str | None = None  # the unit's IMEI 1, or its serial number
    quantity_change: int
    balance_after: int
    movement_type: str
    unit_cost: Decimal | None
    reference_type: str | None
    reference_id: int | None
    reason: str | None
    user_id: int | None
    user_name: str | None = None
    created_at: datetime
