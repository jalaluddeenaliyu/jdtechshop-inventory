"""Suppliers, purchases and supplier payments — owner only."""
from fastapi import APIRouter, Query, Request, Response

from app.core.deps import DB, OwnerUser
from app.schemas.purchasing import (
    PurchaseIn,
    PurchaseOut,
    PurchaseStatusName,
    SupplierCreate,
    SupplierOut,
    SupplierPaymentIn,
    SupplierPaymentOut,
    SupplierUpdate,
)
from app.schemas.settings import Page
from app.services import purchasing as service

router = APIRouter(tags=["purchasing"])


# ---------- Suppliers ----------

@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(_: OwnerUser, db: DB, q: str | None = Query(None, max_length=100),
                   include_inactive: bool = False, owing_only: bool = False) -> list[SupplierOut]:
    return service.list_suppliers(db, q=q, include_inactive=include_inactive, owing_only=owing_only)


@router.get("/suppliers/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: int, _: OwnerUser, db: DB) -> SupplierOut:
    supplier = service.get_supplier(db, supplier_id)
    return service.supplier_out(supplier, service.supplier_balances(db, [supplier.id])[supplier.id])


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(body: SupplierCreate, request: Request, owner: OwnerUser, db: DB) -> SupplierOut:
    supplier = service.create_supplier(db, body, owner, request)
    return service.supplier_out(supplier, service.supplier_balances(db, [supplier.id])[supplier.id])


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: int, body: SupplierUpdate, request: Request, owner: OwnerUser,
                    db: DB) -> SupplierOut:
    supplier = service.update_supplier(db, supplier_id, body, owner, request)
    return service.supplier_out(supplier, service.supplier_balances(db, [supplier.id])[supplier.id])


# ---------- Purchases ----------

@router.get("/purchases", response_model=Page[PurchaseOut])
def list_purchases(
    _: OwnerUser,
    db: DB,
    status: PurchaseStatusName | None = None,
    supplier_id: int | None = None,
    q: str | None = Query(None, max_length=60, description="Reference or supplier invoice number"),
    unpaid_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    return service.list_purchases(db, status_=status, supplier_id=supplier_id, q=q,
                                  unpaid_only=unpaid_only, page=page, page_size=page_size)


@router.get("/purchases/{purchase_id}", response_model=PurchaseOut)
def get_purchase(purchase_id: int, _: OwnerUser, db: DB) -> PurchaseOut:
    return service.purchase_out(db, service.get_purchase(db, purchase_id), with_lines=True)


@router.post("/purchases", response_model=PurchaseOut, status_code=201)
def create_purchase(body: PurchaseIn, request: Request, response: Response, owner: OwnerUser,
                    db: DB) -> PurchaseOut:
    purchase, created = service.create_draft(db, body, owner, request)
    if not created:
        response.status_code = 200  # same form sent twice: here is the draft it already made
    return service.purchase_out(db, purchase, with_lines=True)


@router.put("/purchases/{purchase_id}", response_model=PurchaseOut)
def update_purchase(purchase_id: int, body: PurchaseIn, request: Request, owner: OwnerUser,
                    db: DB) -> PurchaseOut:
    purchase = service.update_draft(db, purchase_id, body, owner, request)
    return service.purchase_out(db, purchase, with_lines=True)


@router.delete("/purchases/{purchase_id}", status_code=204)
def delete_purchase(purchase_id: int, request: Request, owner: OwnerUser, db: DB) -> Response:
    service.delete_draft(db, purchase_id, owner, request)
    return Response(status_code=204)


@router.post("/purchases/{purchase_id}/post", response_model=PurchaseOut)
def post_purchase(purchase_id: int, request: Request, owner: OwnerUser, db: DB) -> PurchaseOut:
    return service.purchase_out(db, service.post_purchase(db, purchase_id, owner, request),
                                with_lines=True)


# ---------- Supplier payments ----------

@router.get("/supplier-payments", response_model=Page[SupplierPaymentOut])
def list_supplier_payments(
    _: OwnerUser,
    db: DB,
    supplier_id: int | None = None,
    purchase_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    return service.list_payments(db, supplier_id=supplier_id, purchase_id=purchase_id, page=page,
                                 page_size=page_size)


@router.post("/supplier-payments", response_model=SupplierPaymentOut, status_code=201)
def record_supplier_payment(body: SupplierPaymentIn, request: Request, response: Response,
                            owner: OwnerUser, db: DB) -> SupplierPaymentOut:
    payment, created = service.record_payment(db, body, owner, request)
    if not created:
        response.status_code = 200  # same form sent twice: here is the payment it already made
    return service.payment_out(db, payment)

