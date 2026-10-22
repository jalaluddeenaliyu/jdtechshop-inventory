from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.catalog import ConditionName, TrackingName, _blank_to_none, _clean_name, to_kobo
from app.schemas.units import _Identifiers, check_identifier_rules


class SaleLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int | None = None
    # Quick sale: an item not in the catalogue yet, typed in at the till with its price.
    # Send quick_name + unit_price instead of product_id; the owner reviews it later.
    quick_name: str | None = Field(default=None, max_length=160)
    unit_id: int | None = None  # required for serial/IMEI products: which phone/laptop
    quantity: int = Field(default=1, ge=1, le=10_000)
    # Discount for the whole line, in naira. The price itself always comes from the product,
    # except for a service whose price is ₦0 ("price set at the till"): then it is typed here.
    discount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    unit_price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)

    _k = field_validator("discount")(to_kobo)

    @field_validator("quick_name", mode="before")
    @classmethod
    def _tidy_name(cls, v):
        return " ".join(v.split()) or None if isinstance(v, str) else v

    @model_validator(mode="after")
    def _product_or_quick(self):
        if (self.product_id is None) == (self.quick_name is None):
            raise ValueError("Each line needs either a product or, for a quick sale, an item name.")
        if self.quick_name is not None:
            if self.unit_price is None:
                raise ValueError(f"Enter the price for {self.quick_name}.")
            if self.unit_id is not None:
                raise ValueError("A quick-sale item has no IMEI/serial to pick.")
        return self


class SalePaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: int
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    reference: str | None = Field(default=None, max_length=80)

    _k = field_validator("amount")(to_kobo)
    _blank = field_validator("reference", mode="before")(_blank_to_none)


class TradeInIn(_Identifiers):
    """Swap deal: the customer's old phone, taken in at an agreed value as part payment.
    Which product it is: an existing phone/laptop product, or a new one (name + selling price)."""

    model_config = ConfigDict(extra="forbid")

    product_id: int | None = None
    new_product_name: str | None = Field(default=None, max_length=160)
    new_product_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    condition: ConditionName = "used"
    value: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    notes: str | None = Field(default=None, max_length=255)

    _blank = field_validator("new_product_name", "notes", mode="before")(_blank_to_none)
    _k = field_validator("value", "new_product_price")(to_kobo)

    @field_validator("new_product_name")
    @classmethod
    def _name(cls, v):
        return _clean_name(v) if v else v

    @model_validator(mode="after")
    def _rules(self):
        if (self.product_id is None) == (self.new_product_name is None):
            raise ValueError("For the swap, choose which phone it is, or type a new product name.")
        if self.new_product_name is not None and self.new_product_price is None:
            raise ValueError("Enter the selling price for the new product.")
        check_identifier_rules(self.serial_number, self.imei1, self.imei2)
        return self


class TradeInOut(BaseModel):
    product_id: int
    product_name: str
    unit_id: int
    imei1: str | None
    imei2: str | None
    serial_number: str | None
    condition: ConditionName
    value: Decimal
    notes: str | None
    returned_to_customer: bool  # the sale was reversed and the phone given back


class SaleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = None  # empty = the walk-in customer
    lines: list[SaleLineIn] = Field(min_length=1, max_length=100)
    # What the customer pays now. Less than the total = the rest is owed (credit sale).
    payments: list[SalePaymentIn] = Field(default_factory=list, max_length=5)
    due_date: date | None = None
    # Swap deal: the customer's old phone, part payment.
    trade_in: TradeInIn | None = None
    # Owner only: record a sale that happened on an earlier day (entering old records).
    # Empty = today.
    sale_date: date | None = None
    notes: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=64)

    _blank = field_validator("notes", mode="before")(_blank_to_none)


class SaleLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    product_sku: str
    tracking_type: TrackingName
    unit_id: int | None
    serial_number: str | None
    imei1: str | None
    imei2: str | None
    condition: ConditionName | None
    quantity: int
    unit_price: Decimal
    discount: Decimal
    line_total: Decimal
    unit_cost_snapshot: Decimal | None  # owner only
    cost_total_snapshot: Decimal | None  # owner only
    returned_quantity: int = 0
    quick_name: str | None = None  # typed in at the till (quick sale)
    review_pending: bool = False  # quick sale not yet reviewed by the owner


class SalePaymentOut(BaseModel):
    id: int
    method_name: str
    amount: Decimal
    reference: str | None
    received_at: datetime


class SaleOut(BaseModel):
    id: int
    receipt_no: str
    customer_id: int
    customer_name: str
    customer_phone: str | None
    is_walk_in: bool
    sold_by_name: str
    sold_at: datetime
    business_date: date
    status: Literal["completed", "reversed"]
    subtotal: Decimal
    discount_total: Decimal
    total: Decimal
    amount_paid: Decimal   # payments (not reversed) less any refunds
    returned_total: Decimal  # value of items brought back
    balance_due: Decimal
    due_date: date | None
    notes: str | None
    item_count: int
    # owner only: (total − returned) − cost of goods kept by the customer (frozen at sale time)
    profit: Decimal | None
    reversed_at: datetime | None = None
    reversed_by_name: str | None = None
    reversal_reason: str | None = None
    reversal_refund: Decimal = Decimal("0")
    lines: list[SaleLineOut] | None = None       # detail only
    payments: list[SalePaymentOut] | None = None  # detail only
    returns: list["SaleReturnSummary"] | None = None  # detail only
    trade_in: TradeInOut | None = None  # swap deal (detail only)


class PosProduct(BaseModel):
    id: int
    name: str
    sku: str
    barcode: str | None
    tracking_type: TrackingName
    condition: ConditionName | None
    selling_price: Decimal
    qty_on_hand: int


class PosUnit(BaseModel):
    id: int
    serial_number: str | None
    imei1: str | None
    imei2: str | None
    condition: ConditionName


class PosResult(BaseModel):
    """A product to add, or — when a serial/IMEI was scanned — the exact unit to add."""

    kind: Literal["product", "unit"]
    product: PosProduct
    unit: PosUnit | None = None


class SaleReturnSummary(BaseModel):
    id: int
    returned_at: datetime
    reason: str
    value_total: Decimal
    refund_amount: Decimal
    recorded_by_name: str | None
    items: list[tuple[int, int, bool]]  # (sale_line_id, quantity, restocked)


SaleOut.model_rebuild()
