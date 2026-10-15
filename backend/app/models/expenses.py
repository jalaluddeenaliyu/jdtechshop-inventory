from datetime import date, datetime
from decimal import Decimal

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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Expense(Base):
    """Money spent running the shop (rent, power, transport, salaries…), for honest net profit.

    Permanent: a mistake is undone with a single reversal (who, when, why), never edited.
    """

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("reversed = (reversed_at IS NOT NULL)", name="reversal_recorded"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    spent_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)  # shop day
    method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id"))
    note: Mapped[str | None] = mapped_column(String(255))
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 nullable=False)
    reversed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reversal_reason: Mapped[str | None] = mapped_column(String(255))
