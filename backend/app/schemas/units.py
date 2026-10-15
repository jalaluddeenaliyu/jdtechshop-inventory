import re
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.catalog import ConditionName, ProductOut, _blank_to_none

UnitStatusName = Literal["in_stock", "reserved", "sold", "returned_to_supplier", "damaged", "missing",
                         "voided"]

SERIAL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9./_-]{2,49}$")


def luhn_ok(digits: str) -> bool:
    """The check digit every real IMEI carries; catches almost every single-digit typo."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def clean_imei(v: str | None) -> str | None:
    if v is None:
        return None
    v = re.sub(r"[\s-]", "", v)
    if not v:
        return None
    if not re.fullmatch(r"[0-9]{15}", v):
        raise ValueError("IMEI must be exactly 15 digits (dial *#06# on the phone to see it).")
    if not luhn_ok(v):
        raise ValueError(f"IMEI {v} is not valid — check for a mistyped digit.")
    return v


def clean_serial(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().upper()
    if not v:
        return None
    if not SERIAL_PATTERN.match(v):
        raise ValueError("Serial number must be 3–50 letters or numbers, with no spaces.")
    return v


class _Identifiers(BaseModel):
    """Shared rules: at least one identifier; IMEI 2 only with IMEI 1; the two IMEIs differ."""

    serial_number: str | None = Field(default=None, max_length=60)
    imei1: str | None = Field(default=None, max_length=30)
    imei2: str | None = Field(default=None, max_length=30)

    _s = field_validator("serial_number")(clean_serial)
    _i = field_validator("imei1", "imei2")(clean_imei)


def check_identifier_rules(serial: str | None, imei1: str | None, imei2: str | None) -> None:
    if imei2 is not None and imei1 is None:
        raise ValueError("Enter IMEI 1 before IMEI 2.")
    if serial is None and imei1 is None:
        raise ValueError("Enter a serial number or an IMEI for each unit.")
    if imei1 is not None and imei1 == imei2:
        raise ValueError("IMEI 1 and IMEI 2 must be different.")


class UnitIn(_Identifiers):
    model_config = ConfigDict(extra="forbid")

    condition: ConditionName = "new"
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    notes: str | None = Field(default=None, max_length=255)

    _blank = field_validator("notes", mode="before")(_blank_to_none)

    @field_validator("unit_cost")
    @classmethod
    def _four_places(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.0001"))

    @model_validator(mode="after")
    def _rules(self):
        check_identifier_rules(self.serial_number, self.imei1, self.imei2)
        return self


class OpeningUnitsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[UnitIn] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=255)

    _blank = field_validator("note", mode="before")(_blank_to_none)


class UnitUpdate(_Identifiers):
    """Correct a typo in a unit's details. Cost and status change only through stock workflows."""

    model_config = ConfigDict(extra="forbid")

    condition: ConditionName | None = None
    notes: str | None = Field(default=None, max_length=255)

    _blank = field_validator("notes", mode="before")(_blank_to_none)

    @model_validator(mode="after")
    def _no_null_condition(self):
        if "condition" in self.model_fields_set and self.condition is None:
            raise ValueError("Condition cannot be empty.")
        return self


class UnitOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    product_sku: str
    serial_number: str | None
    imei1: str | None
    imei2: str | None
    condition: ConditionName
    unit_cost: Decimal | None  # hidden (null) for sales staff
    status: UnitStatusName
    notes: str | None
    created_at: datetime
    updated_at: datetime


class OpeningUnitsResult(BaseModel):
    product: ProductOut
    units: list[UnitOut]
