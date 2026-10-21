"""Correcting a cost price that was entered wrongly.

What changes: the cost of the stock you have NOW — a quantity item's average cost, or the
cost of phones/laptops still in stock — so stock value and the profit on FUTURE sales are
right. What doesn't change: sales already made keep the cost frozen on them when they were
sold (profit already reported never shifts silently), and the stock ledger keeps the cost
each movement had. Every correction is written to the audit log with the old and new cost,
the stock affected, the change in stock value and the reason.
"""
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.models import Product, ProductUnit, TrackingType, User
from app.models.units import ON_HAND_STATUSES
from app.services import stock as stock_service

FOUR = Decimal("0.0001")
TWO = Decimal("0.01")


def _money(v: Decimal) -> str:
    return str(v.quantize(TWO))


def correct_cost(db: Session, product_id: int, new_cost: Decimal, reason: str | None,
                 actor: User, request: Request | None, unit_id: int | None = None) -> dict:
    product: Product = stock_service.lock_product(db, product_id)
    new_cost = new_cost.quantize(FOUR)
    reason = " ".join((reason or "").split())[:200] or None

    if product.tracking_type == TrackingType.SERVICE:
        # A service has no stock: this is simply the cost of one job from now on.
        old = product.avg_cost or Decimal("0")
        if old.quantize(FOUR) == new_cost:
            return {"changed": 0, "value_change": "0.00"}
        product.avg_cost = new_cost
        value_change = Decimal("0")
        details = {"name": product.name, "cost_from": _money(old), "cost_to": _money(new_cost),
                   "quantity": 0, "value_change": "0.00", "reason": reason, "service": True}
        changed = 0
    elif product.tracking_type == TrackingType.QUANTITY:
        if unit_id is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "This product is counted by quantity; it has no separate units.")
        old = product.avg_cost or Decimal("0")
        if old.quantize(FOUR) == new_cost:
            return {"changed": 0, "value_change": "0.00"}
        qty = product.qty_on_hand
        product.avg_cost = new_cost
        value_change = Decimal(qty) * (new_cost - old)
        details = {"name": product.name, "cost_from": _money(old), "cost_to": _money(new_cost),
                   "quantity": qty, "value_change": _money(value_change), "reason": reason}
        changed = qty
    else:
        q = select(ProductUnit).where(ProductUnit.product_id == product.id,
                                      ProductUnit.status.in_(ON_HAND_STATUSES))
        if unit_id is not None:
            q = q.where(ProductUnit.id == unit_id)
        units = list(db.scalars(q.with_for_update().order_by(ProductUnit.id)))
        if not units:
            if unit_id is not None:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "Only a unit that is still in stock can have its cost "
                                    "corrected. A sold unit keeps the cost it was sold at.")
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"No {product.name} in stock, so there is no cost to correct. "
                                "Each phone's cost is entered when it is added.")
        to_change = [u for u in units if u.unit_cost.quantize(FOUR) != new_cost]
        if not to_change:
            return {"changed": 0, "value_change": "0.00"}
        value_change = sum((new_cost - u.unit_cost for u in to_change), Decimal("0"))
        details = {
            "name": product.name, "cost_to": _money(new_cost), "units": len(to_change),
            "value_change": _money(value_change), "reason": reason,
            "unit_costs_from": {(u.imei1 or u.serial_number): _money(u.unit_cost)
                                for u in to_change[:50]},
        }
        for u in to_change:
            u.unit_cost = new_cost
        # Keep the product's average in step with what is on hand (display only for phones).
        db.flush()
        on_hand = list(db.scalars(select(ProductUnit.unit_cost).where(
            ProductUnit.product_id == product.id, ProductUnit.status.in_(ON_HAND_STATUSES))))
        if on_hand:
            product.avg_cost = (sum(on_hand, Decimal("0")) / len(on_hand)).quantize(FOUR)
        changed = len(to_change)

    db.flush()
    audit.record(db, action="product.cost_corrected", user=actor, entity_type="product",
                 entity_id=product.id, details=details, request=request)
    db.commit()
    return {"changed": changed, "value_change": _money(value_change)}
