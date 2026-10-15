"""The shop-wide stock ledger (owner only)."""
from fastapi import APIRouter, Query

from app.core.deps import DB, OwnerUser
from app.models import MovementType
from app.schemas.settings import Page
from app.schemas.stock import StockMovementOut
from app.services import stock as stock_service

router = APIRouter(prefix="/stock-movements", tags=["stock"])


@router.get("", response_model=Page[StockMovementOut])
def list_stock_movements(
    _: OwnerUser,
    db: DB,
    product_id: int | None = None,
    movement_type: MovementType | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    return stock_service.list_movements(db, product_id=product_id, movement_type=movement_type,
                                        page=page, page_size=page_size)
