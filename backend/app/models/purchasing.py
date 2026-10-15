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
    Sequence,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.catalog import Product

# PO-2026-00001, PO-2026-00002 … (the number keeps counting across years).
PURCHASE_REF_SEQ = Sequence("purchase_ref_seq", metadata=Base.metadata)


class PurchaseStatus(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"


class Supplier(TimestampMixin, Base):
    """Who the shop buys from. The balance owed is always calculated, never stored."""

    __tablename__ = "suppliers"
    __table_args__ = (Index("uq_suppliers_name_ci", text("lower(name)"), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Purchase(TimestampMixin, Base):
    """Stock bought from a supplier. A draft changes nothing; posting receives the stock.

    Posted purchases are frozen by a database trigger (corrections are reversals, Phase 3g).
    """

    __tablename__ = "purchases"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'posted', 'reversed')", name="status_valid"),
        CheckConstraint("subtotal >= 0 AND additional_costs >= 0 AND total >= 0", name="amounts_nonneg"),
        CheckConstraint("total = subtotal + additional_costs", name="total_adds_up"),
        CheckConstraint("status = 'draft' OR (posted_at IS NOT NULL AND posted_by_id IS NOT NULL)",
                        name="posted_has_who_and_when"),
        CheckConstraint("(status = 'reversed') = (reversed_at IS NOT NULL)", name="reversal_recorded"),
        # The same supplier invoice cannot be entered twice (ignoring case).
        Index("uq_purchases_supplier_invoice", "supplier_id", text("lower(supplier_invoice_no)"),
              unique=True, postgresql_where=text("supplier_invoice_no IS NOT NULL")),
        Index("ix_purchases_purchase_date", "purchase_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False, index=True)
    supplier_invoice_no: Mapped[str | None] = mapped_column(String(60))
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PurchaseStatus.DRAFT,
                                        index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    # Transport, customs, etc. Spread over the lines by value, so it lands in each item's cost.
    additional_costs: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False,
                                                      default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Reversal (a mistake undone): who, when and why. Set once, never cleared.
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reversal_reason: Mapped[str | None] = mapped_column(String(255))

    supplier: Mapped[Supplier] = relationship(lazy="selectin")
    lines: Mapped[list["PurchaseLine"]] = relationship(
        back_populates="purchase", lazy="selectin", order_by="PurchaseLine.id",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost >= 0 AND allocated_extra_cost >= 0 AND landed_unit_cost >= 0",
                        name="costs_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"),
                                             nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)  # invoice price each
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # quantity × unit_cost
    # This line's share of the purchase's additional costs (whole line, not per item).
    allocated_extra_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False,
                                                          default=Decimal("0"))
    # What each item really cost: unit_cost + its share of additional costs. Used for stock value.
    landed_unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)

    purchase: Mapped[Purchase] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship(lazy="selectin")
    units: Mapped[list["PurchaseLineUnit"]] = relationship(
        lazy="selectin", order_by="PurchaseLineUnit.id", cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PurchaseLineUnit(Base):
    """Serial/IMEI of each phone/laptop on a draft line. Posting turns these into product units."""

    __tablename__ = "purchase_line_units"
    __table_args__ = (
        CheckConstraint("serial_number IS NOT NULL OR imei1 IS NOT NULL", name="has_identifier"),
        CheckConstraint("condition IN ('new', 'used', 'refurbished')", name="condition_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("purchase_lines.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    serial_number: Mapped[str | None] = mapped_column(String(50))
    imei1: Mapped[str | None] = mapped_column(String(15))
    imei2: Mapped[str | None] = mapped_column(String(15))
    condition: Mapped[str] = mapped_column(String(20), nullable=False)


class SupplierPayment(Base):
    """Money paid to a supplier. Append-only: a database trigger blocks edits and deletes."""

    __tablename__ = "supplier_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_supplier_payments_paid_on", "paid_on"),
        CheckConstraint("reversed = (reversed_at IS NOT NULL)", name="reversal_recorded"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False, index=True)
    purchase_id: Mapped[int | None] = mapped_column(ForeignKey("purchases.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(String(255))
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # One-time key from the form: a second submission returns the first payment instead.
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    reversed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Reversal (a mistake undone): who, when and why. Set once, never cleared.
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reversal_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
