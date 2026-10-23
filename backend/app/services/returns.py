"""Customer returns and sale reversals (owner only).

A return brings items back from a completed sale. Its value is what the customer paid for those
items (their share of the line after discount). The value first reduces what is still owed on the
sale; money is given back only for what the customer has then over-paid:

    refund = max(0, net paid − (total − all returns so far))

Items come back into stock at the cost frozen on the sale line (so profit is exactly undone), or,
when they came back damaged, are booked straight out again as damaged — the ledger shows both.

A reversal cancels a whole sale that should never have happened (wrong items keyed, duplicate):
every item goes back, everything paid is refunded, and the sale is kept, marked reversed.
"""
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.clock import business_today, now_utc
from app.models import (
    Customer,
    MovementType,
    PaymentMethod,
    Product,
    ProductUnit,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    SaleStatus,
    TrackingType,
    UnitStatus,
    User,
)
from app.schemas.returns import ReasonIn, SaleReturnIn
from app.services import stock as stock_service
from app.services import swaps
from app.services.debts import sale_money

KOBO = Decimal("0.01")
COST = Decimal("0.0001")
ZERO = Decimal("0.00")


def _naira(amount: Decimal) -> str:
    return f"₦{amount:,.2f}"


def _lock_sale(db: Session, sale_id: int) -> Sale:
    """Lock the customer, then the sale. A return or reversal changes what the customer owes,
    so it must wait for (and be waited for by) a repayment, which locks the same customer row.
    Otherwise both could see the same debt: the payment accepted and the return refunding nothing.
    FOR NO KEY UPDATE still conflicts with the payment's lock but not with a new sale to the
    customer (which only needs a key-share lock), so returns never hold up or deadlock the till."""
    customer_id = db.scalar(select(Sale.customer_id).where(Sale.id == sale_id))
    if customer_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found.")
    db.get(Customer, customer_id, with_for_update={"key_share": True})
    return db.get(Sale, sale_id, with_for_update=True, populate_existing=True)


def returned_quantities(db: Session, sale_line_ids: list[int]) -> dict[int, tuple[int, Decimal]]:
    """{sale_line_id: (quantity already returned, value already returned)}."""
    if not sale_line_ids:
        return {}
    return {lid: (int(q), Decimal(v)) for lid, q, v in db.execute(
        select(SaleReturnLine.sale_line_id, func.sum(SaleReturnLine.quantity),
               func.sum(SaleReturnLine.value))
        .where(SaleReturnLine.sale_line_id.in_(sale_line_ids))
        .group_by(SaleReturnLine.sale_line_id)
    )}


def _not_imported(sale: Sale) -> None:
    if sale.imported:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{sale.receipt_no} was imported from your old records. Its items never came out of "
            "stock in this app, so it can't be returned or reversed here.")


def _refund_method(db: Session, method_id: int | None, amount: Decimal) -> int | None:
    if amount <= 0:
        return None
    if method_id is None:
        raise HTTPException(422, f"{_naira(amount)} must be given back to the customer. "
                                 "Choose how (e.g. Cash).")
    method = db.get(PaymentMethod, method_id)
    if method is None or not method.is_active:
        raise HTTPException(422, "Choose an active payment method for the refund.")
    return method.id


def _bring_back(db: Session, line: SaleLine, quantity: int, restock: bool, actor: User,
                movement_type: MovementType, reference_type: str, reference_id: int,
                reason: str) -> None:
    """Put sold items back into stock (and, if damaged, straight out again as damaged)."""
    product = db.get(Product, line.product_id)  # already locked by the caller
    if product.tracking_type == TrackingType.SERVICE:
        return  # a service has no stock to bring back; only the money is undone
    unit = db.get(ProductUnit, line.unit_id, with_for_update=True) if line.unit_id else None
    swap_receipt = swaps.came_back_in_swap(db, line) if unit else None
    if swap_receipt:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This {product.name} already came back to the shop in a swap "
                            f"({swap_receipt}), so it can't be returned on this receipt too.")
    common = {"user": actor, "unit_id": unit.id if unit else None, "reference_type": reference_type,
              "reference_id": reference_id, "reason": reason}
    if unit:
        unit.status = UnitStatus.IN_STOCK
        unit.sale_line_id = None
    stock_service.record_movement(db, product, quantity_change=quantity,
                                  movement_type=movement_type, unit_cost=line.unit_cost_snapshot,
                                  revalue=movement_type == MovementType.REVERSAL, **common)
    if not restock:
        stock_service.record_movement(db, product, quantity_change=-quantity,
                                      movement_type=MovementType.DAMAGE, **common)
        if unit:
            unit.status = UnitStatus.DAMAGED


def record_return(db: Session, sale_id: int, data: SaleReturnIn, actor: User,
                  request: Request | None) -> tuple[SaleReturn, bool]:
    existing = db.scalar(select(SaleReturn).where(SaleReturn.idempotency_key == data.idempotency_key))
    if existing:
        if existing.sale_id != sale_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This return form was already used.")
        return existing, False

    sale = _lock_sale(db, sale_id)
    _not_imported(sale)
    if sale.status != SaleStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Sale {sale.receipt_no} was reversed, so nothing can be returned.")
    lines = {line.id: line for line in sale.lines}
    wanted: dict[int, int] = {}
    for item in data.lines:
        if item.sale_line_id not in lines:
            raise HTTPException(422, f"That item is not on receipt {sale.receipt_no}.")
        wanted[item.sale_line_id] = wanted.get(item.sale_line_id, 0) + item.quantity
    already = returned_quantities(db, list(wanted))
    for line_id, qty in wanted.items():
        line = lines[line_id]
        left = line.quantity - already.get(line_id, (0, ZERO))[0]
        if qty > left:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Only {left} {line.product.name} from receipt {sale.receipt_no} can still be "
                f"returned ({line.quantity} sold, {line.quantity - left} already returned).",
            )

    for pid in sorted({lines[i.sale_line_id].product_id for i in data.lines}):
        stock_service.lock_product(db, pid)

    # Value of each returned item: its share of the line after discount. The last items of a
    # line take whatever value is left, so the line returns to exactly its total.
    priced = []
    returned_so_far = {lid: v for lid, (_, v) in already.items()}
    returned_qty = {lid: q for lid, (q, _) in already.items()}
    for item in data.lines:
        line = lines[item.sale_line_id]
        done_qty = returned_qty.get(line.id, 0)
        if done_qty + item.quantity == line.quantity:
            value = line.line_total - returned_so_far.get(line.id, ZERO)
        else:
            value = (line.line_total * item.quantity / line.quantity).quantize(KOBO, ROUND_HALF_UP)
        returned_so_far[line.id] = returned_so_far.get(line.id, ZERO) + value
        returned_qty[line.id] = done_qty + item.quantity
        cost = (line.unit_cost_snapshot * item.quantity).quantize(COST)
        priced.append((item, line, value, cost))
    value_total = sum((v for _, _, v, _ in priced), ZERO)
    if value_total <= 0:
        raise HTTPException(422, "These items were free, so there is nothing to return. "
                                 "Use a stock adjustment instead.")

    money = sale_money(db, [sale.id])[sale.id]
    refund = max(ZERO, money.paid - (sale.total - money.returned - value_total))
    method_id = _refund_method(db, data.refund_method_id, refund)

    ret = SaleReturn(sale_id=sale.id, customer_id=sale.customer_id, business_date=business_today(),
                     reason=data.reason, value_total=value_total, refund_amount=refund,
                     refund_method_id=method_id, recorded_by_id=actor.id,
                     idempotency_key=data.idempotency_key)
    db.add(ret)
    try:
        db.flush()
        for item, line, value, cost in priced:
            db.add(SaleReturnLine(return_id=ret.id, sale_line_id=line.id, unit_id=line.unit_id,
                                  quantity=item.quantity, value=value, cost_total=cost,
                                  restocked=item.restock))
            _bring_back(db, line, item.quantity, item.restock, actor, MovementType.CUSTOMER_RETURN,
                        "sale_return", ret.id, data.reason)
        db.flush()
        audit.record(db, action="sale.returned", user=actor, entity_type="sale", entity_id=sale.id,
                     details={"receipt_no": sale.receipt_no, "return_id": ret.id,
                              "value": str(value_total), "refund": str(refund),
                              "reason": data.reason,
                              "items": [f"{line.product.name} × {item.quantity}"
                                        f"{'' if item.restock else ' (damaged)'}"
                                        for item, line, _, _ in priced]},
                     request=request)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(SaleReturn).where(
            SaleReturn.idempotency_key == data.idempotency_key))
        if existing:
            return existing, False
        raise
    db.refresh(ret)
    return ret, True


def reverse_sale(db: Session, sale_id: int, data: ReasonIn, actor: User,
                 request: Request | None) -> Sale:
    """Undo a whole sale. Reversing twice changes nothing."""
    sale = _lock_sale(db, sale_id)
    if sale.status == SaleStatus.REVERSED:
        return sale
    _not_imported(sale)
    if db.scalar(select(SaleReturn.id).where(SaleReturn.sale_id == sale.id).limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Some items from {sale.receipt_no} were already returned. Return the "
                            "remaining items instead of reversing the sale.")
    for pid in sorted({line.product_id for line in sale.lines}):
        stock_service.lock_product(db, pid)
    swaps.undo_trade_in(db, sale, actor, data.reason)  # the customer gets the old phone back
    refund = sale_money(db, [sale.id])[sale.id].paid  # money only: the swap value is undone above
    method_id = _refund_method(db, data.refund_method_id, refund)

    for line in sale.lines:
        _bring_back(db, line, line.quantity, True, actor, MovementType.REVERSAL, "sale", sale.id,
                    data.reason)
    sale.status = SaleStatus.REVERSED
    sale.reversed_at = now_utc()
    sale.reversed_by_id = actor.id
    sale.reversal_reason = data.reason
    sale.reversal_refund = refund
    sale.reversal_refund_method_id = method_id
    db.flush()
    audit.record(db, action="sale.reversed", user=actor, entity_type="sale", entity_id=sale.id,
                 details={"receipt_no": sale.receipt_no, "total": str(sale.total),
                          "refund": str(refund), "reason": data.reason},
                 request=request)
    db.commit()
    db.refresh(sale)
    return sale
