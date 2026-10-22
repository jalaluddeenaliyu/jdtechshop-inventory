"""Quick sales: items sold at the till before they were added to the catalogue.

At the till the cashier types the item's name and price. The sale line points to the built-in
"Quick sale item" (a service: no stock, no cost) and is marked `needs_review`. Later the owner
reviews it once, in one of two ways:

* Link it to the real product (added since, with the sold item included in its stock). The
  stock comes out now, the cost is taken from that product (or the phone/laptop picked), and
  reports then show the real product and the right profit.
* Just enter what it cost — for a one-off item that will never be stocked.

Until then its cost counts as ₦0, so profit for those sales is shown too high; the dashboard
shows how many are waiting. The database allows exactly one review per line (migration 0011).
"""
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.clock import now_utc
from app.models import (
    Customer,
    MovementType,
    Product,
    ProductUnit,
    Sale,
    SaleLine,
    SaleReturnLine,
    SaleStatus,
    TrackingType,
    UnitStatus,
    User,
)
from app.services import stock as stock_service

COST = Decimal("0.0001")


class QuickReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Either link to a product (and, for a phone/laptop, which one) ...
    product_id: int | None = None
    unit_id: int | None = None
    # ... or just record what the item cost (each).
    cost_each: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def _one_way(self):
        if (self.product_id is None) == (self.cost_each is None):
            raise ValueError("Choose the product it was, or enter what it cost.")
        if self.unit_id is not None and self.product_id is None:
            raise ValueError("Pick the product first.")
        return self


class QuickLineOut(BaseModel):
    line_id: int
    sale_id: int
    receipt_no: str
    sold_at: datetime
    business_date: date
    sold_by_name: str
    customer_name: str
    quick_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    reviewed: bool
    reviewed_at: datetime | None
    reviewed_by_name: str | None
    linked_product_name: str | None  # None = cost entered only
    cost_total: Decimal


def _has_returns():
    return exists().where(SaleReturnLine.sale_line_id == SaleLine.id)


def pending_condition():
    """Quick-sale lines still waiting for the owner. A line on a reversed sale, or with items
    brought back, is left as it is: there is nothing left to link."""
    return (SaleLine.needs_review.is_(True), SaleLine.reviewed_at.is_(None),
            Sale.status == SaleStatus.COMPLETED, ~_has_returns())


def pending_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(SaleLine).join(Sale, Sale.id == SaleLine.sale_id)
                     .where(*pending_condition())) or 0


def list_quick_lines(db: Session, reviewed: bool, limit: int = 200) -> list[QuickLineOut]:
    seller = User.__table__.alias("seller")
    reviewer = User.__table__.alias("reviewer")
    q = (select(SaleLine, Sale, Customer.name, seller.c.full_name, reviewer.c.full_name, Product)
         .join(Sale, Sale.id == SaleLine.sale_id)
         .join(Customer, Customer.id == Sale.customer_id)
         .join(seller, seller.c.id == Sale.sold_by_id)
         .outerjoin(reviewer, reviewer.c.id == SaleLine.reviewed_by_id)
         .join(Product, Product.id == SaleLine.product_id))
    if reviewed:
        q = q.where(SaleLine.needs_review.is_(True), SaleLine.reviewed_at.is_not(None)) \
             .order_by(SaleLine.reviewed_at.desc())
    else:
        q = q.where(*pending_condition()).order_by(Sale.business_date, SaleLine.id)
    return [QuickLineOut(
        line_id=line.id, sale_id=sale.id, receipt_no=sale.receipt_no, sold_at=sale.sold_at,
        business_date=sale.business_date, sold_by_name=sold_by or "—",
        customer_name=customer_name, quick_name=line.quick_name, quantity=line.quantity,
        unit_price=line.unit_price, line_total=line.line_total,
        reviewed=line.reviewed_at is not None, reviewed_at=line.reviewed_at,
        reviewed_by_name=reviewed_by,
        linked_product_name=None if product.is_quick_sale else product.name,
        cost_total=line.cost_total_snapshot,
    ) for line, sale, customer_name, sold_by, reviewed_by, product in db.execute(q.limit(limit))]


def _naira(v: Decimal) -> str:
    return f"₦{v:,.2f}"


def review_line(db: Session, line_id: int, data: QuickReviewIn, actor: User,
                request: Request | None) -> QuickLineOut:
    line = db.get(SaleLine, line_id)
    if line is None or not line.needs_review:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quick-sale item not found.")
    # Lock the sale first (as returns and reversals do), then the line.
    sale = db.get(Sale, line.sale_id, with_for_update=True, populate_existing=True)
    line = db.get(SaleLine, line_id, with_for_update=True, populate_existing=True)
    name = line.quick_name
    if line.reviewed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"“{name}” has already been reviewed.")
    if sale.status != SaleStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Sale {sale.receipt_no} was reversed, so “{name}” needs no review.")
    if db.scalar(select(exists().where(SaleReturnLine.sale_line_id == line.id))):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Items of “{name}” were returned, so it can no longer be reviewed.")

    qty = line.quantity
    details: dict = {"receipt_no": sale.receipt_no, "item": name, "quantity": qty}
    if data.product_id is None:
        unit_cost = data.cost_each.quantize(COST)
        details["cost_each"] = str(data.cost_each.quantize(Decimal("0.01")))
    else:
        product: Product = stock_service.lock_product(db, data.product_id)
        if product.is_quick_sale:
            raise HTTPException(422, "Choose the real product this item was.")
        unit = None
        if product.tracking_type == TrackingType.SERVICE:
            if data.unit_id is not None:
                raise HTTPException(422, f"{product.name} is a service: no unit to pick.")
            unit_cost = product.avg_cost
        elif product.tracking_type == TrackingType.QUANTITY:
            if data.unit_id is not None:
                raise HTTPException(422, f"{product.name} is counted by quantity: no unit to pick.")
            if product.qty_on_hand < qty:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{product.name} has only {product.qty_on_hand} in stock, but this sale took "
                    f"{qty}. Add its stock first, counting the {qty} sold on {sale.receipt_no}, "
                    "then link it.")
            unit_cost = product.avg_cost
        else:  # a phone or laptop: one IMEI/serial per line
            if qty != 1:
                raise HTTPException(
                    422, f"This quick sale was for {qty} items, but each {product.name} is linked "
                         "one at a time by its IMEI/serial. Enter what they cost instead.")
            if data.unit_id is None:
                raise HTTPException(422, f"Pick which {product.name} was sold (its IMEI/serial).")
            unit = db.get(ProductUnit, data.unit_id, with_for_update=True, populate_existing=True)
            if unit is None or unit.product_id != product.id:
                raise HTTPException(422, f"That unit is not a {product.name}.")
            if unit.status != UnitStatus.IN_STOCK:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "That phone/laptop is not in stock. Pick another one.")
            unit_cost = unit.unit_cost
        if product.tracking_type != TrackingType.SERVICE:
            stock_service.record_movement(
                db, product, quantity_change=-qty, movement_type=MovementType.SALE, user=actor,
                unit_cost=unit_cost, unit_id=unit.id if unit else None,
                reference_type="sale", reference_id=sale.id,
                reason=f"Quick sale “{name}” on {sale.receipt_no}, linked later")
        if unit is not None:
            unit.status = UnitStatus.SOLD
            unit.sale_line_id = line.id
            line.unit_id = unit.id
        line.product_id = product.id
        details["product"] = product.name
        if unit is not None:
            details["unit"] = unit.imei1 or unit.serial_number
        details["cost_each"] = str(unit_cost.quantize(Decimal("0.01")))

    line.unit_cost_snapshot = unit_cost
    line.cost_total_snapshot = (Decimal(qty) * unit_cost).quantize(COST)
    line.reviewed_at = now_utc()
    line.reviewed_by_id = actor.id
    details["profit"] = str((line.line_total - line.cost_total_snapshot).quantize(Decimal("0.01")))
    db.flush()
    audit.record(db, action="sale.quick_reviewed", user=actor, entity_type="sale",
                 entity_id=sale.id, details=details, request=request)
    db.commit()
    db.expire_all()
    return next(r for r in list_quick_lines(db, reviewed=True) if r.line_id == line_id)
