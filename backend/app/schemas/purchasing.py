from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.schemas.catalog import (
    ConditionName,
    TrackingName,
    _blank_to_none,
    _clean_name,
    _NoNulls,
    to_kobo,
)
from app.schemas.units import _Identifiers, check_identifier_rules

PurchaseStatusName = Literal["draft", "posted", "reversed"]


# ---------- Suppliers ----------

class SupplierOut(BaseModel):
    id: int
    name: str
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    is_active: bool
    purchases_total: Decimal  # all posted purchases, less goods returned
    paid_total: Decimal
    balance: Decimal          # what the shop owes (below zero: the supplier owes the shop)
    created_at: datetime


class _SupplierFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    address: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)

    _blank = field_validator("phone", "email", "address", "notes", mode="before")(_blank_to_none)


class SupplierCreate(_SupplierFields):
    name: str = Field(max_length=120)

    _n = field_validator("name")(_clean_name)


class SupplierUpdate(_SupplierFields, _NoNulls):
    NOT_NULLABLE = ("name", "is_active")

    name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


# ---------- Purchases ----------

class PurchaseUnitIn(_Identifiers):
    model_config = ConfigDict(extra="forbid")

    condition: ConditionName = "new"

    @model_validator(mode="after")
    def _rules(self):
        check_identifier_rules(self.serial_number, self.imei1, self.imei2)
        return self


class PurchaseLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    quantity: int = Field(ge=1, le=100_000)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    # Serial/IMEI products: one entry per unit, and exactly `quantity` of them.
    units: list[PurchaseUnitIn] = Field(default_factory=list, max_length=500)

    @field_validator("unit_cost")
    @classmethod
    def _four_places(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.0001"))


class PurchaseIn(BaseModel):
    """Create or replace a draft purchase. Nothing here touches stock until it is posted."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: int
    supplier_invoice_no: str | None = Field(default=None, max_length=60)
    purchase_date: date
    additional_costs: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=2000)
    lines: list[PurchaseLineIn] = Field(default_factory=list, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=64)

    _blank = field_validator("supplier_invoice_no", "notes", mode="before")(_blank_to_none)
    _k = field_validator("additional_costs")(to_kobo)


class PurchaseUnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    serial_number: str | None
    imei1: str | None
    imei2: str | None
    condition: ConditionName


class StockUnitRef(BaseModel):
    id: int
    label: str  # IMEI 1 or serial number


class PurchaseLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    product_sku: str
    tracking_type: TrackingName
    quantity: int
    unit_cost: Decimal
    line_total: Decimal
    allocated_extra_cost: Decimal
    landed_unit_cost: Decimal
    units: list[PurchaseUnitOut]
    returned_quantity: int = 0
    # Posted serial/IMEI lines: the units from this line still in stock (can go back)
    units_in_stock: list[StockUnitRef] = []


class PurchaseOut(BaseModel):
    id: int
    reference: str
    supplier_id: int
    supplier_name: str
    supplier_invoice_no: str | None
    purchase_date: date
    status: PurchaseStatusName
    subtotal: Decimal
    additional_costs: Decimal
    total: Decimal
    amount_paid: Decimal   # payments recorded against this purchase
    returned_credit: Decimal = Decimal("0")  # goods sent back to the supplier (invoice price)
    balance_due: Decimal
    notes: str | None
    line_count: int
    created_by_name: str | None
    posted_by_name: str | None
    posted_at: datetime | None
    created_at: datetime
    reversed_at: datetime | None = None
    reversal_reason: str | None = None
    lines: list[PurchaseLineOut] | None = None  # detail view only
    returns: list["SupplierReturnSummary"] | None = None  # detail view only


# ---------- Supplier payments ----------

class SupplierPaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: int
    purchase_id: int | None = None  # leave empty for a payment "on account"
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    method_id: int
    paid_on: date | None = None     # defaults to today (shop time)
    reference: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=255)
    # Generated once per form by the browser: a double submit returns the first payment.
    idempotency_key: str = Field(min_length=8, max_length=64)

    _blank = field_validator("reference", "notes", mode="before")(_blank_to_none)
    _k = field_validator("amount")(to_kobo)


class SupplierPaymentOut(BaseModel):
    id: int
    supplier_id: int
    supplier_name: str
    purchase_id: int | None
    purchase_reference: str | None
    amount: Decimal
    method_id: int
    method_name: str
    paid_on: date
    reference: str | None
    notes: str | None
    recorded_by_name: str | None
    reversed: bool
    reversal_reason: str | None = None
    created_at: datetime


class SupplierReturnSummary(BaseModel):
    id: int
    returned_at: datetime
    reason: str
    credit_total: Decimal
    recorded_by_name: str | None
    items: list[str]  # "Charger × 2", "Galaxy A54 (IMEI …)"


PurchaseOut.model_rebuild()

