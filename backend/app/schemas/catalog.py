import re
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TrackingName = Literal["serialized", "quantity", "service"]
ConditionName = Literal["new", "used", "refurbished"]

SKU_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,39}$")
BARCODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,63}$")


def _clean_name(v: str) -> str:
    v = " ".join(v.split())
    if len(v) < 2:
        raise ValueError("Name must have at least 2 characters.")
    return v


def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def to_kobo(v: Decimal | None) -> Decimal | None:
    """Store-exact money: always 2 decimal places (₦1,500 → 1500.00)."""
    return None if v is None else v.quantize(Decimal("0.01"))


def _clean_sku(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.upper()
    if not SKU_PATTERN.match(v):
        raise ValueError(
            "SKU must be up to 40 letters or numbers (dot, dash, slash and underscore allowed, "
            "no spaces)."
        )
    return v


def _clean_barcode(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.upper()
    if not BARCODE_PATTERN.match(v):
        raise ValueError("Barcode must be 3–64 letters or numbers, with no spaces.")
    return v


class _NoNulls(BaseModel):
    """For PATCH bodies: a field may be left out, but these ones may not be sent as null."""

    NOT_NULLABLE: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _reject_nulls(self):
        for field in self.NOT_NULLABLE:
            if field in self.model_fields_set and getattr(self, field) is None:
                label = "SKU" if field == "sku" else field.replace("_", " ").capitalize()
                raise ValueError(f"{label} cannot be empty.")
        return self


# ---------- Categories ----------

class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None
    parent_name: str | None = None
    is_active: bool


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=80)
    parent_id: int | None = None

    _n = field_validator("name")(_clean_name)


class CategoryUpdate(_NoNulls):
    model_config = ConfigDict(extra="forbid")
    NOT_NULLABLE = ("name", "is_active")

    name: str | None = Field(default=None, max_length=80)
    parent_id: int | None = None  # null makes it a top-level category
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


# ---------- Brands ----------

class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool


class BrandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=80)

    _n = field_validator("name")(_clean_name)


class BrandUpdate(_NoNulls):
    model_config = ConfigDict(extra="forbid")
    NOT_NULLABLE = ("name", "is_active")

    name: str | None = Field(default=None, max_length=80)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


# ---------- Products ----------

_PRODUCT_TEXT = ("sku", "barcode", "model", "specifications", "description")


class ProductOut(BaseModel):
    id: int
    sku: str
    barcode: str | None
    name: str
    category_id: int | None
    category_name: str | None
    brand_id: int | None
    brand_name: str | None
    model: str | None
    specifications: str | None
    description: str | None
    tracking_type: TrackingName
    condition: ConditionName | None
    selling_price: Decimal
    avg_cost: Decimal | None  # hidden (null) for sales staff
    # Owner only (null for staff). What the stock on hand cost: quantity items at their weighted
    # average, phones/laptops as the sum of each unit's own cost. cost_each = stock_value / qty
    # (or the last known average when nothing is in stock).
    stock_value: Decimal | None = None
    cost_each: Decimal | None = None
    qty_on_hand: int
    reorder_level: int | None
    effective_reorder_level: int
    is_low_stock: bool
    is_active: bool
    # Whether opening stock can still be entered (see services.catalog.opening_stock_open).
    opening_stock_open: bool
    created_at: datetime
    updated_at: datetime


class ProductCreate(BaseModel):
    """Stock never changes by editing a product. The only stock accepted here is the optional
    opening stock of a NEW quantity-counted product, which is recorded through the stock ledger
    in the same transaction (exactly as the separate opening-stock endpoint would)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=160)
    sku: str | None = Field(default=None, max_length=40)  # blank = generate one
    barcode: str | None = Field(default=None, max_length=64)
    category_id: int | None = None
    brand_id: int | None = None
    model: str | None = Field(default=None, max_length=120)
    specifications: str | None = Field(default=None, max_length=4000)
    description: str | None = Field(default=None, max_length=4000)
    tracking_type: TrackingName
    condition: ConditionName | None = None
    selling_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    reorder_level: int | None = Field(default=None, ge=0, le=100000)
    # Services only: optional cost of one job (materials), so profit is right.
    service_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    # Optional opening stock (quantity-counted products only): both or neither.
    opening_quantity: int | None = Field(default=None, ge=1, le=1_000_000)
    opening_unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)

    _blank = field_validator(*_PRODUCT_TEXT, mode="before")(_blank_to_none)
    _n = field_validator("name")(_clean_name)
    _s = field_validator("sku")(_clean_sku)
    _b = field_validator("barcode")(_clean_barcode)
    _p = field_validator("selling_price")(to_kobo)

    @field_validator("opening_quantity", "opening_unit_cost", "service_cost", mode="before")
    @classmethod
    def _blank_opening(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _opening_rules(self):
        q, c = self.opening_quantity, self.opening_unit_cost
        if (q is None) != (c is None):
            raise ValueError("Enter what each one cost you." if c is None
                             else "Enter how many you have now.")
        if q is not None and self.tracking_type == "service":
            raise ValueError("Services have no stock, so leave the quantity and cost empty. "
                             "Use “Cost per job” for materials.")
        if self.service_cost is not None and self.tracking_type != "service":
            raise ValueError("“Cost per job” is only for services.")
        if q is not None and self.tracking_type == "serialized":
            raise ValueError("Phones and other serial/IMEI items are added unit by unit, each "
                             "with its IMEI and cost, after the product is created.")
        if c is not None:
            self.opening_unit_cost = c.quantize(Decimal("0.0001"))
        return self


class ProductUpdate(_NoNulls):
    model_config = ConfigDict(extra="forbid")
    NOT_NULLABLE = ("name", "sku", "tracking_type", "selling_price", "is_active")

    name: str | None = Field(default=None, max_length=160)
    sku: str | None = Field(default=None, max_length=40)
    barcode: str | None = Field(default=None, max_length=64)
    category_id: int | None = None
    brand_id: int | None = None
    model: str | None = Field(default=None, max_length=120)
    specifications: str | None = Field(default=None, max_length=4000)
    description: str | None = Field(default=None, max_length=4000)
    tracking_type: TrackingName | None = None
    condition: ConditionName | None = None
    selling_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    reorder_level: int | None = Field(default=None, ge=0, le=100000)  # null = shop default
    is_active: bool | None = None

    _blank = field_validator(*_PRODUCT_TEXT, mode="before")(_blank_to_none)
    _s = field_validator("sku")(_clean_sku)
    _b = field_validator("barcode")(_clean_barcode)
    _p = field_validator("selling_price")(to_kobo)

    @field_validator("name")
    @classmethod
    def _n(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


class CostCorrectionIn(BaseModel):
    """Correct the cost price of the stock on hand (see services/cost_correction.py)."""

    model_config = ConfigDict(extra="forbid")

    cost_each: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    reason: str | None = Field(default=None, max_length=200)
    # Phones/laptops: correct just this unit (otherwise every unit of the product in stock).
    unit_id: int | None = None

    @field_validator("cost_each", mode="before")
    @classmethod
    def _clean_money(cls, v):
        if isinstance(v, str):
            v = v.replace(",", "").replace("₦", "").strip()
        return v
