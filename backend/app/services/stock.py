"""Stock ledger — the only code allowed to change how many of a product the shop holds.

Every change to `products.qty_on_hand` goes through `record_movement()`, which writes an
append-only `stock_movements` row in the same transaction. The database double-checks this
at commit time: it refuses to save a product whose quantity differs from its movements.

Callers must lock the product first with `lock_product()` so two requests (two cashiers,
a double click) cannot read the same quantity and both act on it.
"""
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.models import MovementType, Product, ProductUnit, StockMovement, TrackingType, User
from app.schemas.stock import OpeningStockIn, StockMovementOut

COST_PLACES = Decimal("0.0001")

# Movements that bring stock in at a known cost and therefore update the weighted average.
# (A customer return comes back at the cost frozen on the sale line.)
COST_RECEIVING = {MovementType.OPENING, MovementType.PURCHASE, MovementType.CUSTOMER_RETURN,
                  MovementType.TRADE_IN}


def lock_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id, with_for_update=True, populate_existing=True)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    return product


def weighted_average(old_qty: int, old_avg: Decimal, added_qty: int, added_cost: Decimal) -> Decimal:
    """(old qty × old average + received qty × received cost) ÷ new qty, to 4 decimal places."""
    total = old_qty + added_qty
    value = Decimal(old_qty) * old_avg + Decimal(added_qty) * added_cost
    return (value / Decimal(total)).quantize(COST_PLACES, rounding=ROUND_HALF_UP)


def average_without(old_qty: int, old_avg: Decimal, removed_qty: int, removed_cost: Decimal) -> Decimal:
    """The average after taking out stock that came in at a known cost (undoing a receipt):
    (old qty × old average − removed qty × its cost) ÷ remaining qty. Never below zero."""
    remaining = old_qty - removed_qty
    if remaining <= 0:
        return Decimal("0.0000")
    value = Decimal(old_qty) * old_avg - Decimal(removed_qty) * removed_cost
    return max(Decimal("0"), value / Decimal(remaining)).quantize(COST_PLACES, rounding=ROUND_HALF_UP)


def record_movement(
    db: Session,
    product: Product,
    *,
    quantity_change: int,
    movement_type: MovementType,
    user: User | None,
    unit_cost: Decimal | None = None,
    unit_id: int | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
    reason: str | None = None,
    revalue: bool = False,
) -> StockMovement:
    """Change a (locked) product's stock and write the ledger row. Does not commit.

    `revalue=True` with a unit cost re-works the weighted average for this movement too: stock
    added at that cost is averaged in, stock removed at that cost is taken back out (used when
    a purchase is reversed or goods go back to the supplier).
    """
    if quantity_change == 0:
        raise ValueError("A stock movement must change the quantity.")
    new_qty = product.qty_on_hand + quantity_change
    if new_qty < 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Not enough stock of {product.name}: {product.qty_on_hand} available, "
            f"{-quantity_change} needed.",
        )

    if movement_type in COST_RECEIVING:
        if quantity_change < 0 or unit_cost is None:
            raise ValueError(f"{movement_type} movements must add stock at a known unit cost.")
        product.avg_cost = weighted_average(product.qty_on_hand, product.avg_cost,
                                            quantity_change, unit_cost)
    elif revalue:
        if unit_cost is None:
            raise ValueError("Re-valuing stock needs the unit cost of the movement.")
        if quantity_change > 0:
            product.avg_cost = weighted_average(product.qty_on_hand, product.avg_cost,
                                                quantity_change, unit_cost)
        else:
            product.avg_cost = average_without(product.qty_on_hand, product.avg_cost,
                                               -quantity_change, unit_cost)
    elif unit_cost is None:
        unit_cost = product.avg_cost  # stock going out is valued at the current average

    product.qty_on_hand = new_qty
    movement = StockMovement(
        product_id=product.id,
        unit_id=unit_id,
        quantity_change=quantity_change,
        balance_after=new_qty,
        movement_type=movement_type,
        unit_cost=unit_cost,
        reference_type=reference_type,
        reference_id=reference_id,
        reason=reason,
        user_id=user.id if user else None,
    )
    db.add(movement)
    return movement


def has_movements(db: Session, product_id: int) -> bool:
    return db.scalar(
        select(StockMovement.id).where(StockMovement.product_id == product_id).limit(1)
    ) is not None


def record_opening_stock(db: Session, product_id: int, data: OpeningStockIn, actor: User,
                         request: Request | None) -> Product:
    """The stock a product already had when the shop started using the system. Once only."""
    product = lock_product(db, product_id)
    if product.tracking_type == TrackingType.SERVICE:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{product.name} is a service, so it has no stock to enter.")
    if product.tracking_type == TrackingType.SERIALIZED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Opening stock for serial/IMEI-tracked products is entered unit by unit, "
            "with each unit's serial or IMEI number.",
        )
    if not product.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This product is turned off. Turn it on before adding stock.")
    if has_movements(db, product.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Opening stock can only be entered once, before any other stock movement. "
            "Use a stock adjustment to correct the quantity.",
        )
    add_opening_stock(db, product, data.quantity, data.unit_cost, note=data.note, actor=actor,
                      request=request)
    db.commit()
    return product


def add_opening_stock(db: Session, product: Product, quantity: int, unit_cost: Decimal, *,
                      note: str | None, actor: User, request: Request | None) -> StockMovement:
    """Write the opening-stock ledger row and its audit entry. Does not commit."""
    movement = record_movement(db, product, quantity_change=quantity,
                               movement_type=MovementType.OPENING, user=actor,
                               unit_cost=unit_cost, reason=note)
    db.flush()
    audit.record(db, action="stock.opening", user=actor, entity_type="product",
                 entity_id=product.id,
                 details={"quantity": quantity, "unit_cost": str(unit_cost),
                          "movement_id": movement.id},
                 request=request)
    return movement


def list_movements(db: Session, *, product_id: int | None = None,
                   movement_type: MovementType | None = None, page: int = 1,
                   page_size: int = 50) -> dict:
    """Newest first, with product and user names, as a Page."""
    conditions = []
    if product_id is not None:
        conditions.append(StockMovement.product_id == product_id)
    if movement_type is not None:
        conditions.append(StockMovement.movement_type == movement_type)
    unit_label = func.coalesce(ProductUnit.imei1, ProductUnit.serial_number)
    rows = db.execute(
        select(StockMovement, Product.name, Product.sku, User.full_name, unit_label)
        .join(Product, Product.id == StockMovement.product_id)
        .outerjoin(User, User.id == StockMovement.user_id)
        .outerjoin(ProductUnit, ProductUnit.id == StockMovement.unit_id)
        .where(*conditions)
        .order_by(StockMovement.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = []
    for movement, product_name, sku, user_name, label in rows:
        item = StockMovementOut.model_validate(movement)
        item.product_name, item.product_sku, item.user_name = product_name, sku, user_name
        item.unit_label = label
        items.append(item)
    total = db.scalar(select(func.count()).select_from(StockMovement).where(*conditions)) or 0
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def ledger_mismatches(db: Session) -> list[tuple[int, str, int, int]]:
    """Products whose cached quantity differs from their movements: (id, sku, cached, ledger).

    Should always be empty — the database refuses such commits. Used by `app.cli check`.
    """
    ledger = (
        select(StockMovement.product_id, func.sum(StockMovement.quantity_change).label("total"))
        .group_by(StockMovement.product_id)
        .subquery()
    )
    total = func.coalesce(ledger.c.total, 0)
    rows = db.execute(
        select(Product.id, Product.sku, Product.qty_on_hand, total)
        .outerjoin(ledger, ledger.c.product_id == Product.id)
        .where(Product.qty_on_hand != total)
    ).all()
    return [tuple(r) for r in rows]
