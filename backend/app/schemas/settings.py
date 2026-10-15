from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class BusinessSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_name: str
    slogan: str | None
    address: str | None
    phone: str | None
    email: str | None
    receipt_footer: str | None
    currency_code: str
    default_reorder_level: int
    staff_max_discount_percent: Decimal
    allow_staff_credit_sales: bool
    updated_at: datetime


class BusinessSettingsUpdate(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    slogan: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    receipt_footer: str | None = Field(default=None, max_length=255)
    default_reorder_level: int = Field(ge=0, le=100000)
    staff_max_discount_percent: Decimal = Field(ge=0, le=100, decimal_places=2)
    allow_staff_credit_sales: bool

    _blank = field_validator("slogan", "address", "phone", "email", "receipt_footer", mode="before")(
        _blank_to_none
    )

    @field_validator("business_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())


class PaymentMethodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    sort_order: int


class PaymentMethodCreate(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    sort_order: int = Field(default=0, ge=0, le=1000)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Name must have at least 2 characters.")
        return v


class PaymentMethodUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=50)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    user_name: str | None = None
    action: str
    entity_type: str | None
    entity_id: str | None
    details: dict | None
    ip_address: str | None
    created_at: datetime


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
