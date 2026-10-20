"""Products and their stock.

Staff can look products up (for selling) but never see cost prices. Only the owner can
create or change products, enter opening stock, or read the stock ledger.
"""
from typing import Literal

from fastapi import APIRouter, Query, Request

from app.core.deps import DB, CurrentUser, OwnerUser
from app.schemas.catalog import CostCorrectionIn, ProductCreate, ProductOut, ProductUpdate
from app.schemas.settings import Page
from app.schemas.stock import OpeningStockIn, StockMovementOut
from app.services import catalog as catalog_service
from app.services import cost_correction
from app.services import product_removal
from app.services import stock as stock_service
from app.services.catalog import ProductSort, StockFilter

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=Page[ProductOut])
def list_products(
    user: CurrentUser,
    db: DB,
    q: str | None = Query(None, max_length=100, description="Name, SKU, barcode or model"),
    category_id: int | None = None,
    brand_id: int | None = None,
    tracking_type: Literal["serialized", "quantity"] | None = None,
    stock: StockFilter | None = None,
    include_inactive: bool = False,
    sort: ProductSort = "name",
    descending: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    products, total, default_reorder = catalog_service.list_products(
        db, q=q, category_id=category_id, brand_id=brand_id, tracking_type=tracking_type,
        stock=stock, include_inactive=include_inactive, sort=sort, descending=descending,
        page=page, page_size=page_size,
    )
    open_map = catalog_service.opening_stock_open(db, products)
    unit_values = catalog_service.unit_stock_values(db, products) if user.is_owner else None
    items = [catalog_service.product_out(p, default_reorder, show_cost=user.is_owner,
                                         opening_open=open_map[p.id], unit_values=unit_values)
             for p in products]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, user: CurrentUser, db: DB) -> ProductOut:
    product = catalog_service.get_product(db, product_id)
    return catalog_service.product_out_one(db, product, show_cost=user.is_owner)


@router.post("", response_model=ProductOut, status_code=201)
def create_product(body: ProductCreate, request: Request, owner: OwnerUser, db: DB) -> ProductOut:
    product = catalog_service.create_product(db, body, owner, request)
    return catalog_service.product_out_one(db, product, True)


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(product_id: int, body: ProductUpdate, request: Request, owner: OwnerUser,
                   db: DB) -> ProductOut:
    product = catalog_service.update_product(db, product_id, body, owner, request)
    return catalog_service.product_out_one(db, product, True)


@router.delete("/{product_id}")
def delete_product(product_id: int, request: Request, owner: OwnerUser, db: DB,
                   reason: str | None = Query(None, max_length=200)) -> dict:
    """Delete a product added by mistake (see services/product_removal.py for the rules)."""
    return product_removal.remove_product(db, product_id, reason, owner, request)


@router.post("/{product_id}/cost", response_model=ProductOut)
def correct_cost(product_id: int, body: CostCorrectionIn, request: Request, owner: OwnerUser,
                 db: DB) -> ProductOut:
    """Correct a wrongly entered cost price for the stock on hand. Past sales keep their cost."""
    cost_correction.correct_cost(db, product_id, body.cost_each, body.reason, owner, request,
                                 unit_id=body.unit_id)
    return catalog_service.product_out_one(db, catalog_service.get_product(db, product_id), True)


@router.post("/{product_id}/opening-stock", response_model=ProductOut, status_code=201)
def record_opening_stock(product_id: int, body: OpeningStockIn, request: Request,
                         owner: OwnerUser, db: DB) -> ProductOut:
    product = stock_service.record_opening_stock(db, product_id, body, owner, request)
    return catalog_service.product_out_one(db, product, True)


@router.get("/{product_id}/movements", response_model=Page[StockMovementOut])
def product_movements(
    product_id: int,
    _: OwnerUser,
    db: DB,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    catalog_service.get_product(db, product_id)  # 404 if it does not exist
    return stock_service.list_movements(db, product_id=product_id, page=page,
                                        page_size=page_size)
