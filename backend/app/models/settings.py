from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BusinessSettings(TimestampMixin, Base):
    """Exactly one row (id = 1) holding shop-wide settings."""

    __tablename__ = "business_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint(
            "staff_max_discount_percent >= 0 AND staff_max_discount_percent <= 100",
            name="discount_range",
        ),
        CheckConstraint("default_reorder_level >= 0", name="reorder_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    business_name: Mapped[str] = mapped_column(String(120), nullable=False, default="JD TechShop")
    slogan: Mapped[str | None] = mapped_column(String(160), default="Delivering Happiness")
    address: Mapped[str | None] = mapped_column(String(255), default="Sokoto, Nigeria")
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(120))
    receipt_footer: Mapped[str | None] = mapped_column(
        String(255), default="Thank you for shopping with us. Goods sold in good condition."
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    default_reorder_level: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    staff_max_discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("5.00")
    )
    allow_staff_credit_sales: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class PaymentMethod(TimestampMixin, Base):
    __tablename__ = "payment_methods"
    # Case-insensitive uniqueness: "Cash" and "cash" cannot both exist.
    __table_args__ = (Index("uq_payment_methods_name_ci", text("lower(name)"), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The built-in "Swap (old phone)" kind (migration 0012): the value of a phone taken in at
    # the till. Not money, never offered on cash screens, left out of cash figures.
    is_trade_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                              server_default="false")
