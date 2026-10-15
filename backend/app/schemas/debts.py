from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.catalog import _blank_to_none, to_kobo


class CustomerPaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    sale_id: int | None = None  # empty = towards the balance, oldest sales first
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    method_id: int
    received_on: date | None = None  # defaults to today (shop time)
    reference: str | None = Field(default=None, max_length=80)
    # Generated once per form by the browser: a double submit returns the first payment.
    idempotency_key: str = Field(min_length=8, max_length=64)

    _k = field_validator("amount")(to_kobo)
    _blank = field_validator("reference", mode="before")(_blank_to_none)


class AllocationOut(BaseModel):
    sale_id: int
    receipt_no: str
    amount: Decimal


class CustomerPaymentOut(BaseModel):
    id: int
    reference_no: str  # PAY-000123, printed on the payment receipt
    customer_id: int
    customer_name: str
    customer_phone: str | None
    amount: Decimal
    method_name: str
    received_on: date
    received_at: datetime
    reference: str | None
    recorded_by_name: str | None
    at_till: bool       # taken as part of a sale
    reversed: bool
    reversal_reason: str | None = None
    allocations: list[AllocationOut]
    customer_owes_now: Decimal  # at the time of reading, not of the payment


class UnpaidSaleOut(BaseModel):
    sale_id: int
    receipt_no: str
    business_date: date
    total: Decimal
    returned: Decimal  # value of items brought back
    paid: Decimal      # after any refunds
    balance: Decimal
    due_date: date | None
    overdue: bool


class CustomerDebtOut(BaseModel):
    customer_id: int
    customer_name: str
    sales_total: Decimal
    paid_total: Decimal
    owed: Decimal
    overdue: Decimal
    unpaid_sales: list[UnpaidSaleOut]
