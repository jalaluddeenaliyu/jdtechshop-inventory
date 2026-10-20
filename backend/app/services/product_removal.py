"""Removing a product that was added by mistake.

The stock ledger is permanent, so "delete" means one of three things:

1. Nothing has ever happened to the product (no stock, no units, not on any purchase,
   sale or adjustment): the product row is deleted outright. Audited.
2. The only stock it ever had is the opening stock entered when it was added (no
   purchases, sales, returns or adjustments): that stock is taken back out with a
   "reversal" movement in the ledger (so the history shows what happened and why),
   phones/laptops are voided and their IMEIs/serials released (so the same phones can be
   added again under the right product), and the product is turned off. Audited.
3. Anything else (it was bought, sold, returned or adjusted): refused. Those records feed
   sales, profit and supplier balances, so the product can only be turned off.
"""
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.models import (
    MovementType,
    Product,
    ProductUnit,
    PurchaseLine,
    SaleLine,
    StockAdjustmentLine,
    StockMovement,
    TrackingType,
    User,
)
from app.models.units import UnitStatus
from app.services import stock as stock_service

DEFAULT_REASON = "Added by mistake"


def _exists(db: Session, query) -> bool:
    return db.scalar(query.limit(1)) is not None


def remove_product(db: Session, product_id: int, reason: str | None, actor: User,
                   request: Request | None) -> dict:
    product: Product = stock_service.lock_product(db, product_id)
    name = product.name
    reason = " ".join((reason or "").split())[:200] or DEFAULT_REASON

    if _exists(db, select(StockMovement.id).where(
            StockMovement.product_id == product.id,
            StockMovement.movement_type != MovementType.OPENING)) \
            or _exists(db, select(SaleLine.id).where(SaleLine.product_id == product.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{name} has been bought, sold, returned or adjusted, so it can't be deleted: "
            "your sales, profit and supplier records depend on it. To stop selling it, "
            "edit it and untick “Product is on sale”.")
    if _exists(db, select(PurchaseLine.id).where(PurchaseLine.product_id == product.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{name} is on a purchase (it may be a draft). Remove it from that purchase "
            "first, then delete it.")
    if _exists(db, select(StockAdjustmentLine.id).where(
            StockAdjustmentLine.product_id == product.id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{name} has a stock adjustment request, so it can't be deleted. "
            "Edit it and untick “Product is on sale” instead.")

    has_stock_history = _exists(db, select(StockMovement.id).where(
        StockMovement.product_id == product.id))
    has_units = _exists(db, select(ProductUnit.id).where(ProductUnit.product_id == product.id))

    if not has_stock_history and not has_units:
        details = {"name": name, "sku": product.sku, "selling_price": str(product.selling_price),
                   "reason": reason}
        db.delete(product)
        db.flush()
        audit.record(db, action="product.deleted", user=actor, entity_type="product",
                     entity_id=product_id, details=details, request=request)
        db.commit()
        return {"result": "deleted", "name": name, "quantity_removed": 0,
                "value_removed": "0.00"}

    # Only opening stock: take it back out, keep the history, turn the product off.
    note = f"Removed: {reason}"
    removed_qty = 0
    removed_value = Decimal("0")
    if product.tracking_type == TrackingType.SERIALIZED:
        units = list(db.scalars(select(ProductUnit).where(
            ProductUnit.product_id == product.id).with_for_update().order_by(ProductUnit.id)))
        for unit in units:
            if unit.status != UnitStatus.IN_STOCK:
                continue
            stock_service.record_movement(db, product, quantity_change=-1,
                                          movement_type=MovementType.REVERSAL, user=actor,
                                          unit_cost=unit.unit_cost, unit_id=unit.id,
                                          reference_type="product_removal",
                                          reference_id=product.id, reason=note)
            # Release the IMEIs/serial so the same phone can be registered correctly.
            was = ", ".join(f"{label} {value}" for label, value in (
                ("IMEI 1", unit.imei1), ("IMEI 2", unit.imei2), ("serial", unit.serial_number))
                if value)
            unit.notes = f"{note}. Was {was}."[:255]
            unit.imei1 = None
            unit.imei2 = None
            unit.serial_number = f"VOID-{unit.id}"
            unit.status = UnitStatus.VOIDED
            removed_qty += 1
            removed_value += unit.unit_cost
    elif product.qty_on_hand > 0:
        removed_qty = product.qty_on_hand
        removed_value = Decimal(removed_qty) * product.avg_cost
        stock_service.record_movement(db, product, quantity_change=-removed_qty,
                                      movement_type=MovementType.REVERSAL, user=actor,
                                      unit_cost=product.avg_cost,
                                      reference_type="product_removal",
                                      reference_id=product.id, reason=note)
    product.is_active = False
    db.flush()
    value = removed_value.quantize(Decimal("0.01"))
    audit.record(db, action="product.removed", user=actor, entity_type="product",
                 entity_id=product.id,
                 details={"name": name, "sku": product.sku, "quantity_removed": removed_qty,
                          "value_removed": str(value), "reason": reason},
                 request=request)
    db.commit()
    return {"result": "removed", "name": name, "quantity_removed": removed_qty,
            "value_removed": str(value)}
