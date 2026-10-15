"""Returns, reversals and stock adjustments.

Returns, reversals and approving adjustments are owner-only. Anyone may request an adjustment.
"""
from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from app.core.deps import DB, CurrentUser, OwnerUser
from app.schemas.debts import CustomerPaymentOut
from app.schemas.purchasing import PurchaseOut, SupplierPaymentOut
from app.schemas.returns import (
    AdjustmentIn,
    AdjustmentOut,
    DecisionIn,
    ReasonIn,
    SaleReturnIn,
    SupplierReturnIn,
)
from app.schemas.sales import SaleOut
from app.schemas.settings import Page
from app.services import adjustments as adjustment_service
from app.services import debts as debt_service
from app.services import purchasing as purchasing_service
from app.services import returns as return_service
from app.services import sales as sale_service
from app.services import supplier_returns as supplier_return_service

router = APIRouter(tags=["returns"])


# ---------- Customer returns and sale reversal ----------

@router.post("/sales/{sale_id}/returns", response_model=SaleOut, status_code=201)
def return_items(sale_id: int, body: SaleReturnIn, request: Request, response: Response,
                 owner: OwnerUser, db: DB) -> SaleOut:
    _, created = return_service.record_return(db, sale_id, body, owner, request)
    if not created:
        response.status_code = 200  # same form sent twice: nothing more was returned
    return sale_service.sale_out(db, sale_service.get_sale(db, sale_id), show_cost=True, detail=True)


@router.post("/sales/{sale_id}/reverse", response_model=SaleOut)
def reverse_sale(sale_id: int, body: ReasonIn, request: Request, owner: OwnerUser,
                 db: DB) -> SaleOut:
    sale = return_service.reverse_sale(db, sale_id, body, owner, request)
    return sale_service.sale_out(db, sale, show_cost=True, detail=True)


# ---------- Supplier returns and purchase reversal ----------

@router.post("/purchases/{purchase_id}/returns", response_model=PurchaseOut, status_code=201)
def return_to_supplier(purchase_id: int, body: SupplierReturnIn, request: Request,
                       response: Response, owner: OwnerUser, db: DB) -> PurchaseOut:
    _, created = supplier_return_service.record_return(db, purchase_id, body, owner, request)
    if not created:
        response.status_code = 200
    return purchasing_service.purchase_out(db, purchasing_service.get_purchase(db, purchase_id),
                                           with_lines=True)


@router.post("/purchases/{purchase_id}/reverse", response_model=PurchaseOut)
def reverse_purchase(purchase_id: int, body: ReasonIn, request: Request, owner: OwnerUser,
                     db: DB) -> PurchaseOut:
    purchase = supplier_return_service.reverse_purchase(db, purchase_id, body, owner, request)
    return purchasing_service.purchase_out(db, purchase, with_lines=True)


# ---------- Payment reversals ----------

@router.post("/customer-payments/{payment_id}/reverse", response_model=CustomerPaymentOut)
def reverse_customer_payment(payment_id: int, body: ReasonIn, request: Request, owner: OwnerUser,
                             db: DB) -> CustomerPaymentOut:
    payment = debt_service.reverse_payment(db, payment_id, body.reason, owner, request)
    return debt_service.payment_out(db, payment)


@router.post("/supplier-payments/{payment_id}/reverse", response_model=SupplierPaymentOut)
def reverse_supplier_payment(payment_id: int, body: ReasonIn, request: Request, owner: OwnerUser,
                             db: DB) -> SupplierPaymentOut:
    payment = purchasing_service.reverse_payment(db, payment_id, body.reason, owner, request)
    return purchasing_service.payment_out(db, payment)


# ---------- Stock adjustments ----------

@router.get("/stock-adjustments", response_model=Page[AdjustmentOut])
def list_adjustments(user: CurrentUser, db: DB,
                     status: Literal["pending", "approved", "rejected"] | None = None,
                     page: int = Query(1, ge=1),
                     page_size: int = Query(25, ge=1, le=200)) -> dict:
    return adjustment_service.list_adjustments(db, viewer=user, status_=status, page=page,
                                               page_size=page_size)


@router.post("/stock-adjustments", response_model=AdjustmentOut, status_code=201)
def request_adjustment(body: AdjustmentIn, request: Request, user: CurrentUser,
                       db: DB) -> AdjustmentOut:
    adj = adjustment_service.create_adjustment(db, body, user, request)
    return adjustment_service.adjustment_out(db, adj)


@router.post("/stock-adjustments/{adjustment_id}/approve", response_model=AdjustmentOut)
def approve_adjustment(adjustment_id: int, body: DecisionIn, request: Request, owner: OwnerUser,
                       db: DB) -> AdjustmentOut:
    adj = adjustment_service.approve(db, adjustment_id, body, owner, request)
    return adjustment_service.adjustment_out(db, adj)


@router.post("/stock-adjustments/{adjustment_id}/reject", response_model=AdjustmentOut)
def reject_adjustment(adjustment_id: int, body: DecisionIn, request: Request, owner: OwnerUser,
                      db: DB) -> AdjustmentOut:
    adj = adjustment_service.reject(db, adjustment_id, body, owner, request)
    return adjustment_service.adjustment_out(db, adj)
