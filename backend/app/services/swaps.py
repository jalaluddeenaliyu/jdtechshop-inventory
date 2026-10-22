"""Swap deals: the customer's old phone taken in at the till as part payment.

The agreed value is recorded as a payment of the built-in "Swap (old phone)" kind, so the
sale's balance, debts and receipts work as for any payment, while cash figures leave it out.
The phone goes into stock as a unit whose cost is that value, so selling it later shows the
real profit. A phone the shop sold before comes back as the same unit (its IMEI is already
registered); undoing the swap restores it as it was.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import now_utc
from app.models import (
    Customer,
    CustomerPayment,
    ImeiRegistry,
    MovementType,
    PaymentMethod,
    Product,
    ProductUnit,
    Sale,
    SaleLine,
    SaleTradeIn,
    TrackingType,
    UnitStatus,
    User,
)
from app.schemas.catalog import ProductCreate
from app.schemas.sales import TradeInIn, TradeInOut
from app.services import stock as stock_service
from app.services.units import check_identifiers_free

STATUS_WORDS = {
    UnitStatus.IN_STOCK: "in stock right now", UnitStatus.RESERVED: "reserved",
    UnitStatus.DAMAGED: "recorded as damaged", UnitStatus.MISSING: "recorded as missing",
    UnitStatus.RETURNED_TO_SUPPLIER: "recorded as returned to the supplier",
}


def trade_in_method(db: Session) -> PaymentMethod:
    method = db.scalar(select(PaymentMethod).where(PaymentMethod.is_trade_in.is_(True)))
    if method is None:  # created by migration 0012; should never happen
        raise HTTPException(500, "The swap payment kind is missing. Run the database migrations.")
    return method


@dataclass
class SwapPlan:
    data: TradeInIn
    product_id: int | None       # existing product to lock (None: a new product is created)
    reuse_unit_id: int | None    # a phone the shop sold before


def plan_trade_in(db: Session, data: TradeInIn) -> SwapPlan:
    """Work out which product and unit the swap touches, before anything is locked."""
    found: dict[int, ProductUnit] = {}
    if data.serial_number:
        for u in db.scalars(select(ProductUnit).where(ProductUnit.serial_number == data.serial_number)):
            found[u.id] = u
    imeis = [i for i in (data.imei1, data.imei2) if i]
    if imeis:
        for u in db.scalars(select(ProductUnit).join(ImeiRegistry, ImeiRegistry.unit_id == ProductUnit.id)
                            .where(ImeiRegistry.imei.in_(imeis))):
            found[u.id] = u
    if len(found) > 1:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Swap: these IMEI/serial numbers belong to different phones already in "
                            "the system. Check the numbers.")
    if not found:
        if data.product_id is not None:
            product = db.get(Product, data.product_id)
            if product is None or product.is_quick_sale:
                raise HTTPException(422, "Swap: that product does not exist.")
            if product.tracking_type != TrackingType.SERIALIZED:
                raise HTTPException(422, f"Swap: {product.name} is not a phone/laptop product "
                                         "(counted by IMEI or serial).")
        return SwapPlan(data, data.product_id, None)
    unit = next(iter(found.values()))
    if unit.status != UnitStatus.SOLD:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Swap: this phone is already registered to “{unit.product.name}” and "
                            f"is {STATUS_WORDS.get(unit.status, unit.status)}. Check the IMEI/serial.")
    # Sold by the shop before: it comes back as the same unit of the same product, so every
    # number typed must be one of that phone's.
    mine = {unit.serial_number, unit.imei1, unit.imei2} - {None}
    typed = {data.serial_number, data.imei1, data.imei2} - {None}
    if not typed <= mine:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Swap: this is the {unit.product.name} you sold before, but some of the "
                            "numbers typed don't match it. Check the IMEI/serial.")
    return SwapPlan(data, unit.product_id, unit.id)


def check_value(data: TradeInIn, total: Decimal, other_payments: Decimal) -> None:
    if data.value > total:
        raise HTTPException(
            422, f"The swap value (₦{data.value:,.2f}) is more than this sale (₦{total:,.2f}). "
                 "A swap can only pay for part or all of what the customer buys.")
    if data.value + other_payments > total:
        raise HTTPException(
            422, f"The swap (₦{data.value:,.2f}) plus the other payments (₦{other_payments:,.2f}) is "
                 f"more than the total of ₦{total:,.2f}. Lower the payments.")


def apply_trade_in(db: Session, plan: SwapPlan, sale: Sale, customer: Customer, day: date,
                   actor: User, request: Request | None) -> dict:
    """Take the phone into stock and record its value as a payment. Does not commit.
    The product (if existing) is already locked by the caller."""
    data = plan.data
    if plan.product_id is None:
        from app.services.catalog import create_product  # avoid an import cycle
        product = create_product(db, ProductCreate(
            name=data.new_product_name, tracking_type="serialized",
            selling_price=data.new_product_price), actor, request, commit=False)
    else:
        product = db.get(Product, plan.product_id)

    previous = {}
    if plan.reuse_unit_id is not None:
        unit = db.get(ProductUnit, plan.reuse_unit_id, with_for_update=True, populate_existing=True)
        if unit.status != UnitStatus.SOLD:
            raise HTTPException(status.HTTP_409_CONFLICT, "Swap: that phone changed a moment ago. Try again.")
        previous = {"previous_sale_line_id": unit.sale_line_id, "previous_unit_cost": unit.unit_cost,
                    "previous_condition": unit.condition}
        unit.status = UnitStatus.IN_STOCK
        unit.sale_line_id = None
        unit.unit_cost = data.value
        unit.condition = data.condition
    else:
        check_identifiers_free(db, [("Swap: ", data.serial_number, data.imei1, data.imei2)])
        unit = ProductUnit(product_id=product.id, serial_number=data.serial_number, imei1=data.imei1,
                           imei2=data.imei2, condition=data.condition, unit_cost=data.value,
                           notes=data.notes, status=UnitStatus.IN_STOCK, created_by_id=actor.id)
        db.add(unit)
    db.flush()
    stock_service.record_movement(db, product, quantity_change=1, movement_type=MovementType.TRADE_IN,
                                  user=actor, unit_cost=data.value, unit_id=unit.id,
                                  reference_type="sale", reference_id=sale.id,
                                  reason=f"Swap on {sale.receipt_no}")
    payment = CustomerPayment(customer_id=customer.id, sale_id=sale.id, amount=data.value,
                              method_id=trade_in_method(db).id, business_date=day,
                              reference=(unit.imei1 or unit.serial_number), recorded_by_id=actor.id)
    db.add(payment)
    db.flush()
    db.add(SaleTradeIn(sale_id=sale.id, unit_id=unit.id, payment_id=payment.id, value=data.value,
                       product_name=product.name, imei1=unit.imei1, imei2=unit.imei2,
                       serial_number=unit.serial_number, condition=data.condition,
                       notes=data.notes, **previous))
    db.flush()
    return {"product": product.name, "identifier": unit.imei1 or unit.serial_number,
            "value": str(data.value), "condition": data.condition,
            **({"sold_by_shop_before": True} if previous else {})}


def trade_in_out(db: Session, sale: Sale) -> TradeInOut | None:
    t = db.scalar(select(SaleTradeIn).where(SaleTradeIn.sale_id == sale.id))
    if t is None:
        return None
    unit = db.get(ProductUnit, t.unit_id)
    return TradeInOut(product_id=unit.product_id, product_name=t.product_name, unit_id=t.unit_id,
                      imei1=t.imei1, imei2=t.imei2, serial_number=t.serial_number,
                      condition=t.condition, value=t.value, notes=t.notes,
                      returned_to_customer=sale.reversed_at is not None)


def undo_trade_in(db: Session, sale: Sale, actor: User, reason: str) -> None:
    """Sale reversal: the customer gets the old phone back. Does not commit."""
    t = db.scalar(select(SaleTradeIn).where(SaleTradeIn.sale_id == sale.id))
    if t is None:
        return
    unit = db.get(ProductUnit, t.unit_id, with_for_update=True, populate_existing=True)
    if unit.status != UnitStatus.IN_STOCK:
        label = unit.imei1 or unit.serial_number or t.imei1 or t.serial_number
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The customer's old phone ({t.product_name}, {label}) from this swap is no longer in "
            "stock, so it can't be given back and the sale can't be reversed. Use “Return items” instead.")
    product = stock_service.lock_product(db, unit.product_id)
    stock_service.record_movement(db, product, quantity_change=-1, movement_type=MovementType.REVERSAL,
                                  user=actor, unit_cost=unit.unit_cost, unit_id=unit.id,
                                  reference_type="sale", reference_id=sale.id, revalue=True,
                                  reason=f"Swap undone, phone given back: {reason}")
    if t.previous_sale_line_id is not None:
        # The shop's own earlier sale: back to how it was.
        unit.status = UnitStatus.SOLD
        unit.sale_line_id = t.previous_sale_line_id
        unit.unit_cost = t.previous_unit_cost
        unit.condition = t.previous_condition
    else:
        # The phone leaves the shop's records; its IMEI/serial can be registered again later.
        unit.imei1 = None
        unit.imei2 = None
        unit.serial_number = f"VOID-{unit.id}"
        unit.status = UnitStatus.VOIDED
    payment = db.get(CustomerPayment, t.payment_id, with_for_update=True)
    payment.reversed = True
    payment.reversed_at = now_utc()
    payment.reversed_by_id = actor.id
    payment.reversal_reason = f"Swap undone with the sale: {reason}"[:255]
    db.flush()


def came_back_in_swap(db: Session, line: SaleLine) -> str | None:
    """A phone sold on `line` that has since come back to the shop in a swap can't also be
    returned on that old sale. Returns the receipt of the swap, if so."""
    if not line.unit_id:
        return None
    t = db.scalar(select(SaleTradeIn).where(SaleTradeIn.previous_sale_line_id == line.id,
                                            SaleTradeIn.unit_id == line.unit_id))
    if t is None:
        return None
    sale = db.get(Sale, t.sale_id)
    return None if sale.reversed_at is not None else sale.receipt_no

