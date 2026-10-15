import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.catalog import _blank_to_none, _clean_name, _NoNulls

PHONE_HELP = "Enter a phone number like 0803 123 4567 (or +44… for a number outside Nigeria)."


def normalize_phone(v: str | None) -> str | None:
    """One stored form per number: Nigerian numbers as 0XXXXXXXXXX, others as +<digits>.

    "+234 803 123 4567", "2348031234567", "803-123-4567" and "0803 123 4567" all become
    "08031234567", so the same customer cannot be added twice under different spellings.
    """
    if v is None:
        return None
    digits = re.sub(r"[\s().-]", "", v)
    if not digits:
        return None
    if digits.startswith("+234"):
        digits = "0" + digits[4:]
    elif digits.startswith("234") and len(digits) == 13:
        digits = "0" + digits[3:]
    elif re.fullmatch(r"[789][01][0-9]{8}", digits):
        digits = "0" + digits
    if re.fullmatch(r"0[0-9]{10}", digits) or re.fullmatch(r"\+[0-9]{8,15}", digits):
        return digits
    raise ValueError(PHONE_HELP)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    is_walk_in: bool
    is_active: bool
    created_at: datetime
    owed: Decimal = Decimal("0.00")     # what the customer owes now (always calculated)
    overdue: Decimal = Decimal("0.00")  # part of it past its due date


class _CustomerFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    address: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)

    _blank = field_validator("email", "address", "notes", mode="before")(_blank_to_none)
    _p = field_validator("phone")(normalize_phone)


class CustomerCreate(_CustomerFields):
    name: str = Field(max_length=120)

    _n = field_validator("name")(_clean_name)


class CustomerUpdate(_CustomerFields, _NoNulls):
    NOT_NULLABLE = ("name", "is_active")

    name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)
