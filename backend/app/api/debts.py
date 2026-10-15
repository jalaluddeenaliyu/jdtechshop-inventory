"""Customer debts and repayments — owner and sales staff (staff take repayments at the till)."""
from datetime import date

from fastapi import APIRouter, Query, Request, Response

from app.core.deps import DB, CurrentUser
from app.schemas.debts import CustomerDebtOut, CustomerPaymentIn, CustomerPaymentOut
from app.schemas.settings import Page
from app.services import customers as customer_service
from app.services import debts as service

router = APIRouter(tags=["debts"])


@router.get("/customers/{customer_id}/debt", response_model=CustomerDebtOut)
def customer_debt(customer_id: int, _: CurrentUser, db: DB) -> CustomerDebtOut:
    customer = customer_service.get_customer(db, customer_id)
    balance = service.customer_balances(db, [customer.id])[customer.id]
    return CustomerDebtOut(
        customer_id=customer.id, customer_name=customer.name, sales_total=balance.sales_total,
        paid_total=balance.paid_total, owed=balance.owed, overdue=balance.overdue,
        unpaid_sales=service.unpaid_sales(db, customer.id),
    )


@router.post("/customer-payments", response_model=CustomerPaymentOut, status_code=201)
def record_customer_payment(body: CustomerPaymentIn, request: Request, response: Response,
                            user: CurrentUser, db: DB) -> CustomerPaymentOut:
    payment, created = service.record_payment(db, body, user, request)
    if not created:
        response.status_code = 200  # same form sent twice: here is the payment it already made
    return service.payment_out(db, payment)


@router.get("/customer-payments", response_model=Page[CustomerPaymentOut])
def list_customer_payments(
    _: CurrentUser,
    db: DB,
    customer_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> dict:
    return service.list_payments(db, customer_id=customer_id, date_from=date_from,
                                 date_to=date_to, page=page, page_size=page_size)


@router.get("/customer-payments/{payment_id}", response_model=CustomerPaymentOut)
def get_customer_payment(payment_id: int, _: CurrentUser, db: DB) -> CustomerPaymentOut:
    return service.payment_out(db, service.get_payment(db, payment_id))
