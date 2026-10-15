import re
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TrackingName = Literal["serialized", "quantity"]
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
    """Stock quantity and cost are NOT accepted here: they only change through the stock ledger."""

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

    _blank = field_validator(*_PRODUCT_TEXT, mode="before")(_blank_to_none)
    _n = field_validator("name")(_clean_name)
    _s = field_validator("sku")(_clean_sku)
    _b = field_validator("barcode")(_clean_barcode)
    _p = field_validator("selling_price")(to_kobo)


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
