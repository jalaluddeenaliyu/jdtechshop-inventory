"""The till (POS) and sales history. Owner and sales staff; only the owner sees cost and profit."""
from datetime import date

from fastapi import APIRouter, Query, Request, Response

from app.core.deps import DB, CurrentUser
from app.schemas.sales import PosResult, SaleIn, SaleOut
from app.schemas.settings import Page
from app.services import sales as service

router = APIRouter(tags=["sales"])


@router.get("/pos/search", response_model=list[PosResult])
def pos_search(_: CurrentUser, db: DB,
               q: str = Query(..., min_length=1, max_length=100,
                              description="Name, SKU, barcode, serial number or IMEI")) -> list[PosResult]:
    return service.pos_search(db, q)


@router.post("/sales", response_model=SaleOut, status_code=201)
def create_sale(body: SaleIn, request: Request, response: Response, user: CurrentUser,
                db: DB) -> SaleOut:
    sale, created = service.create_sale(db, body, user, request)
    if not created:
        response.status_code = 200  # same cart sent twice: here is the sale it already made
    return service.sale_out(db, sale, show_cost=user.is_owner, detail=True)


@router.get("/sales", response_model=Page[SaleOut])
def list_sales(
    user: CurrentUser,
    db: DB,
    date_from: date | None = None,
    date_to: date | None = None,
    customer_id: int | None = None,
    sold_by_id: int | None = None,
    q: str | None = Query(None, max_length=60, description="Receipt number or customer name"),
    unpaid_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    return service.list_sales(db, date_from=date_from, date_to=date_to, customer_id=customer_id,
                              sold_by_id=sold_by_id, q=q, unpaid_only=unpaid_only, page=page,
                              page_size=page_size, show_cost=user.is_owner)


@router.get("/sales/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: int, user: CurrentUser, db: DB) -> SaleOut:
    return service.sale_out(db, service.get_sale(db, sale_id), show_cost=user.is_owner, detail=True)
