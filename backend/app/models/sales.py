from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.catalog import Product
from app.models.customers import Customer


class SaleStatus(StrEnum):
    COMPLETED = "completed"
    REVERSED = "reversed"


class ReceiptCounter(Base):
    """Receipt numbers restart every shop day: JDT-YYMMDD-0001, -0002 …"""

    __tablename__ = "receipt_counters"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False)


class Sale(Base):
    """A completed sale. Never edited or deleted (database trigger); corrections are reversals."""

    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("status IN ('completed', 'reversed')", name="status_valid"),
        CheckConstraint("subtotal >= 0 AND discount_total >= 0 AND total >= 0", name="amounts_nonneg"),
        CheckConstraint("total = subtotal - discount_total", name="total_adds_up"),
        CheckConstraint("amount_paid_at_sale >= 0 AND amount_paid_at_sale <= total",
                        name="paid_not_more_than_total"),
        CheckConstraint("(status = 'reversed') = (reversed_at IS NOT NULL)", name="reversal_recorded"),
        CheckConstraint("reversal_refund >= 0", name="reversal_refund_nonneg"),
        Index("ix_sales_business_date", "business_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_no: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    sold_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                              nullable=False)
    # The shop day (Africa/Lagos) the sale belongs to — what "today" means in reports.
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SaleStatus.COMPLETED)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount_paid_at_sale: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)  # for credit sales
    notes: Mapped[str | None] = mapped_column(Text)
    # One-time key from the till: the same cart submitted twice records one sale.
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Reversal (a mistake undone): who, when and why. Set once, never cleared.
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reversal_reason: Mapped[str | None] = mapped_column(String(255))
    # Money given back when the sale was reversed (everything that had been paid on it).
    reversal_refund: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False,
                                                     default=Decimal("0"))
    reversal_refund_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"))

    customer: Mapped[Customer] = relationship(lazy="selectin")
    lines: Mapped[list["SaleLine"]] = relationship(lazy="selectin", order_by="SaleLine.id")


class SaleLine(Base):
    __tablename__ = "sale_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_id IS NULL OR quantity = 1", name="unit_sold_one_at_a_time"),
        CheckConstraint("unit_price >= 0 AND discount >= 0 AND discount <= quantity * unit_price",
                        name="discount_within_price"),
        CheckConstraint("line_total = quantity * unit_price - discount", name="line_total_adds_up"),
        CheckConstraint("unit_cost_snapshot >= 0 AND cost_total_snapshot >= 0", name="costs_nonneg"),
        CheckConstraint("needs_review = (quick_name IS NOT NULL)", name="quick_line_named"),
        CheckConstraint("reviewed_at IS NULL OR needs_review", name="review_only_quick_lines"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # whole line, in naira
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Frozen at the moment of sale, so later cost changes never change past profit.
    unit_cost_snapshot: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    cost_total_snapshot: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    # Quick sale: an item typed in at the till before it was in the catalogue. It points to the
    # built-in "Quick sale item" until the owner reviews it once (links it to the real product,
    # or enters what it cost). The database allows only that one change (migration 0011).
    quick_name: Mapped[str | None] = mapped_column(String(160))
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                               server_default="false")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    product: Mapped[Product] = relationship(lazy="selectin")


class CustomerPayment(Base):
    """Money received from a customer — at the till or later. Append-only (database trigger)."""

    __tablename__ = "customer_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_customer_payments_business_date", "business_date"),
        CheckConstraint("reversed = (reversed_at IS NOT NULL)", name="reversal_recorded"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    # Set for payments taken at the till. Later payments use allocations instead.
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(80))
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Payments taken at the till are covered by the sale's key; later payments carry their own.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    reversed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Reversal (a mistake undone): who, when and why. Set once, never cleared.
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reversal_reason: Mapped[str | None] = mapped_column(String(255))


class PaymentAllocation(Base):
    """Which sales a later payment paid off. A payment for one sale has one allocation; a payment
    "towards the balance" is spread over the oldest unpaid sales first. Append-only."""

    __tablename__ = "customer_payment_allocations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint("payment_id", "sale_id", name="uq_allocation_payment_sale"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("customer_payments.id"), nullable=False,
                                            index=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
