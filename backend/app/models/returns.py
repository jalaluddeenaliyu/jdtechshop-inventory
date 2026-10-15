"""Customer returns, supplier returns and stock adjustments. All permanent records."""
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SaleReturn(Base):
    """Items a customer brought back. The value first reduces what is owed on the sale; only money
    the customer had over-paid as a result is refunded (refund_amount)."""

    __tablename__ = "sale_returns"
    __table_args__ = (
        CheckConstraint("value_total > 0", name="value_positive"),
        CheckConstraint("refund_amount >= 0 AND refund_amount <= value_total", name="refund_within_value"),
        CheckConstraint("refund_amount = 0 OR refund_method_id IS NOT NULL", name="refund_has_method"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    returned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    value_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    refund_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"))
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    lines: Mapped[list["SaleReturnLine"]] = relationship(lazy="selectin", order_by="SaleReturnLine.id")


class SaleReturnLine(Base):
    __tablename__ = "sale_return_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_id IS NULL OR quantity = 1", name="unit_one_at_a_time"),
        CheckConstraint("value >= 0 AND cost_total >= 0", name="amounts_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("sale_returns.id"), nullable=False, index=True)
    sale_line_id: Mapped[int] = mapped_column(ForeignKey("sale_lines.id"), nullable=False, index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # what it was sold for
    cost_total: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)  # frozen sale cost
    restocked: Mapped[bool] = mapped_column(Boolean, nullable=False)  # False = came back damaged


class SupplierReturn(Base):
    """Items sent back to a supplier. The supplier is credited the invoice price."""

    __tablename__ = "supplier_returns"
    __table_args__ = (CheckConstraint("credit_total > 0", name="credit_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"), nullable=False, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    returned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    credit_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    lines: Mapped[list["SupplierReturnLine"]] = relationship(lazy="selectin",
                                                             order_by="SupplierReturnLine.id")


class SupplierReturnLine(Base):
    __tablename__ = "supplier_return_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_id IS NULL OR quantity = 1", name="unit_one_at_a_time"),
        CheckConstraint("credit_value >= 0", name="credit_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("supplier_returns.id"), nullable=False,
                                           index=True)
    purchase_line_id: Mapped[int] = mapped_column(ForeignKey("purchase_lines.id"), nullable=False,
                                                  index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class AdjustmentStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class StockAdjustment(Base):
    """A stock correction. Only an owner-approved adjustment moves stock."""

    __tablename__ = "stock_adjustments"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
        CheckConstraint("status = 'pending' OR (decided_by_id IS NOT NULL AND decided_at IS NOT NULL)",
                        name="decision_has_who_and_when"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AdjustmentStatus.PENDING,
                                        index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                   nullable=False)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(255))

    lines: Mapped[list["StockAdjustmentLine"]] = relationship(lazy="selectin",
                                                              order_by="StockAdjustmentLine.id")


class StockAdjustmentLine(Base):
    """For quantity items: a change (+/−). For serial/IMEI items: one unit and its new status."""

    __tablename__ = "stock_adjustment_lines"
    __table_args__ = (
        CheckConstraint("quantity_change <> 0", name="nonzero"),
        CheckConstraint("kind IN ('damage', 'missing', 'adjustment')", name="kind_valid"),
        CheckConstraint("unit_id IS NULL OR quantity_change IN (1, -1)", name="unit_moves_one"),
        CheckConstraint("(unit_id IS NULL) = (unit_new_status IS NULL)", name="unit_has_new_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adjustment_id: Mapped[int] = mapped_column(ForeignKey("stock_adjustments.id"), nullable=False,
                                               index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("product_units.id"))
    unit_new_status: Mapped[str | None] = mapped_column(String(30))
    quantity_change: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # damage | missing | adjustment
