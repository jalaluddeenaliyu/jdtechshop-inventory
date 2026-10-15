"""Customer debts and repayments.

What a customer owes is always calculated, never typed in:
    owed = Σ completed sale totals − Σ payments (not reversed)

A sale's own balance is its total minus the payments taken at the till (customer_payments.sale_id)
minus the parts of later payments allocated to it. A later payment is either for one sale, or
"towards the balance" — then it is spread over the oldest unpaid sales first, so every receipt's
balance stays right. A payment can never be more than what is owed: the customer row is locked
while it is checked, so two cashiers cannot both take the last ₦5,000.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.clock import business_today, now_utc
from app.models import (
    Customer,
    CustomerPayment,
    PaymentAllocation,
    PaymentMethod,
    Sale,
    SaleReturn,
    SaleStatus,
    User,
)
from app.schemas.debts import (
    AllocationOut,
    CustomerPaymentIn,
    CustomerPaymentOut,
    UnpaidSaleOut,
)

ZERO = Decimal("0.00")


def _naira(amount: Decimal) -> str:
    return f"₦{amount:,.2f}"


# ---------- How much is paid / owed ----------

def sale_paid_expr():
    """Correlated SQL: total paid on Sale.id (till payments + allocations), reversed excluded."""
    direct = (select(func.coalesce(func.sum(CustomerPayment.amount), 0))
              .where(CustomerPayment.sale_id == Sale.id, CustomerPayment.reversed.is_(False))
              .scalar_subquery())
    allocated = (select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
                 .join(CustomerPayment, CustomerPayment.id == PaymentAllocation.payment_id)
                 .where(PaymentAllocation.sale_id == Sale.id, CustomerPayment.reversed.is_(False))
                 .scalar_subquery())
    return direct + allocated


def sale_returned_expr():
    """Correlated SQL: value of items returned from Sale.id."""
    return (select(func.coalesce(func.sum(SaleReturn.value_total), 0))
            .where(SaleReturn.sale_id == Sale.id).scalar_subquery())


def sale_refunded_expr():
    """Correlated SQL: money given back on Sale.id (return refunds and a reversal refund)."""
    return (select(func.coalesce(func.sum(SaleReturn.refund_amount), 0))
            .where(SaleReturn.sale_id == Sale.id).scalar_subquery()) + Sale.reversal_refund


def sale_balance_expr():
    """Correlated SQL: what is still owed on Sale.id. A reversed sale owes nothing.

    owed = total − value returned − (paid − refunded)
    """
    return case((Sale.status == SaleStatus.REVERSED, 0),
                else_=Sale.total - sale_returned_expr() - sale_paid_expr() + sale_refunded_expr())


@dataclass
class SaleMoney:
    paid: Decimal       # paid, after refunds
    returned: Decimal   # value of items returned
    balance: Decimal    # still owed


def sale_money(db: Session, sale_ids: list[int]) -> dict[int, SaleMoney]:
    if not sale_ids:
        return {}
    rows = db.execute(select(
        Sale.id, sale_paid_expr() - sale_refunded_expr(), sale_returned_expr(), sale_balance_expr(),
    ).where(Sale.id.in_(sale_ids))).all()
    kobo = Decimal("0.01")
    return {sid: SaleMoney(paid=Decimal(paid).quantize(kobo), returned=Decimal(ret).quantize(kobo),
                           balance=Decimal(bal).quantize(kobo))
            for sid, paid, ret, bal in rows}


def paid_by_sale(db: Session, sale_ids: list[int]) -> dict[int, Decimal]:
    """Net paid per sale (payments − refunds)."""
    return {sid: m.paid for sid, m in sale_money(db, sale_ids).items()}


@dataclass
class Balance:
    sales_total: Decimal = ZERO  # completed sales, less returns
    paid_total: Decimal = ZERO   # payments, less refunds
    owed: Decimal = ZERO
    overdue: Decimal = ZERO


def customer_balances(db: Session, customer_ids: list[int]) -> dict[int, Balance]:
    """Owed = the sum of each completed sale's balance (reversed sales owe nothing)."""
    out = {cid: Balance() for cid in customer_ids}
    if not customer_ids:
        return out
    today = business_today()
    balance = sale_balance_expr()
    completed = Sale.status == SaleStatus.COMPLETED
    for cid, sold, paid, owed, overdue in db.execute(
        select(
            Sale.customer_id,
            func.sum(case((completed, Sale.total - sale_returned_expr()), else_=0)),
            func.sum(case((completed, sale_paid_expr() - sale_refunded_expr()), else_=0)),
            func.sum(balance),
            func.sum(case((and_(completed, Sale.due_date < today), balance), else_=0)),
        ).where(Sale.customer_id.in_(customer_ids)).group_by(Sale.customer_id)
    ):
        kobo = Decimal("0.01")
        out[cid] = Balance(sales_total=Decimal(sold).quantize(kobo),
                           paid_total=Decimal(paid).quantize(kobo),
                           owed=Decimal(owed).quantize(kobo), overdue=Decimal(overdue).quantize(kobo))
    return out


def owing_customer_ids():
    """SQL: ids of customers who owe something (for the "only customers who owe" filter)."""
    return select(Sale.customer_id).where(sale_balance_expr() > 0).distinct()


def unpaid_sales(db: Session, customer_id: int) -> list[UnpaidSaleOut]:
    """The customer's sales that still have something owing, oldest first."""
    balance = sale_balance_expr()
    rows = db.execute(
        select(Sale, sale_paid_expr() - sale_refunded_expr(), sale_returned_expr(), balance)
        .where(Sale.customer_id == customer_id, Sale.status == SaleStatus.COMPLETED, balance > 0)
        .order_by(Sale.id)
    ).all()
    today = business_today()
    return [UnpaidSaleOut(sale_id=s.id, receipt_no=s.receipt_no, business_date=s.business_date,
                          total=s.total, returned=Decimal(r), paid=Decimal(p), balance=Decimal(b),
                          due_date=s.due_date, overdue=bool(s.due_date and s.due_date < today))
            for s, p, r, b in rows]


# ---------- Taking a payment ----------

def payment_out(db: Session, pay: CustomerPayment) -> CustomerPaymentOut:
    customer = db.get(Customer, pay.customer_id)
    method = db.get(PaymentMethod, pay.method_id)
    recorder = db.scalar(select(User.full_name).where(User.id == pay.recorded_by_id)) \
        if pay.recorded_by_id else None
    if pay.sale_id is not None:  # taken at the till
        allocations = [AllocationOut(sale_id=pay.sale_id, amount=pay.amount,
                                     receipt_no=db.scalar(select(Sale.receipt_no)
                                                          .where(Sale.id == pay.sale_id)))]
    else:
        allocations = [AllocationOut(sale_id=a.sale_id, receipt_no=no, amount=a.amount)
                       for a, no in db.execute(
                           select(PaymentAllocation, Sale.receipt_no)
                           .join(Sale, Sale.id == PaymentAllocation.sale_id)
                           .where(PaymentAllocation.payment_id == pay.id)
                           .order_by(PaymentAllocation.id))]
    return CustomerPaymentOut(
        id=pay.id, reference_no=f"PAY-{pay.id:06d}", customer_id=customer.id,
        customer_name=customer.name, customer_phone=customer.phone, amount=pay.amount,
        method_name=method.name, received_on=pay.business_date, received_at=pay.received_at,
        reference=pay.reference, recorded_by_name=recorder, at_till=pay.sale_id is not None,
        reversed=pay.reversed, reversal_reason=pay.reversal_reason, allocations=allocations,
        customer_owes_now=customer_balances(db, [customer.id])[customer.id].owed,
    )


def _replay(db: Session, data: CustomerPaymentIn) -> CustomerPayment | None:
    existing = db.scalar(select(CustomerPayment).where(
        CustomerPayment.idempotency_key == data.idempotency_key))
    if existing is None:
        return None
    if existing.customer_id != data.customer_id or existing.amount != data.amount:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This payment form was already used for a different payment. "
                            "Reload the page and try again.")
    return existing


def record_payment(db: Session, data: CustomerPaymentIn, actor: User,
                   request: Request | None) -> tuple[CustomerPayment, bool]:
    """Returns (payment, created). Re-sending the same form returns the first payment."""
    existing = _replay(db, data)
    if existing:
        return existing, False

    customer = db.get(Customer, data.customer_id, with_for_update=True)
    if customer is None:
        raise HTTPException(422, "That customer does not exist.")
    if customer.is_walk_in:
        raise HTTPException(422, "Walk-in sales are always paid in full, so nothing is owed.")
    method = db.get(PaymentMethod, data.method_id)
    if method is None or not method.is_active:
        raise HTTPException(422, "Choose an active payment method.")
    received_on = data.received_on or business_today()
    if received_on > business_today():
        raise HTTPException(422, "The payment date cannot be in the future.")

    unpaid = unpaid_sales(db, customer.id)
    owed = sum((s.balance for s in unpaid), ZERO)
    if data.sale_id is not None:
        target = next((s for s in unpaid if s.sale_id == data.sale_id), None)
        if target is None:
            sale = db.get(Sale, data.sale_id)
            if sale is None or sale.customer_id != customer.id:
                raise HTTPException(422, f"That sale is not {customer.name}'s.")
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Receipt {sale.receipt_no} is already fully paid.")
        if data.amount > target.balance:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"That is more than is owed on receipt {target.receipt_no} "
                f"({_naira(target.balance)}). Overpayments are not allowed.",
            )
        plan = [(target.sale_id, data.amount)]
    else:
        if data.amount > owed:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"That is more than {customer.name} owes ({_naira(owed)}). "
                "Overpayments are not allowed.",
            )
        plan, left = [], data.amount
        for s in unpaid:  # oldest sales first
            part = min(left, s.balance)
            plan.append((s.sale_id, part))
            left -= part
            if left == 0:
                break

    payment = CustomerPayment(
        customer_id=customer.id, sale_id=None, amount=data.amount, method_id=method.id,
        business_date=received_on, reference=data.reference, recorded_by_id=actor.id,
        idempotency_key=data.idempotency_key, reversed=False,
    )
    db.add(payment)
    try:
        db.flush()
        for sale_id, part in plan:
            db.add(PaymentAllocation(payment_id=payment.id, sale_id=sale_id, amount=part))
        db.flush()
    except IntegrityError:  # the same form submitted twice at the same moment
        db.rollback()
        existing = _replay(db, data)
        if existing:
            return existing, False
        raise
    receipts = dict(db.execute(select(Sale.id, Sale.receipt_no)
                               .where(Sale.id.in_([sid for sid, _ in plan]))).all())
    audit.record(db, action="customer_payment.recorded", user=actor,
                 entity_type="customer_payment", entity_id=payment.id,
                 details={"customer": customer.name, "amount": str(payment.amount),
                          "method": method.name, "owed_before": str(owed),
                          "allocations": [f"{receipts[sid]}: {part}" for sid, part in plan]},
                 request=request)
    db.commit()
    db.refresh(payment)
    return payment, True


def get_payment(db: Session, payment_id: int) -> CustomerPayment:
    payment = db.get(CustomerPayment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found.")
    return payment


def list_payments(db: Session, *, customer_id: int | None, date_from: date | None,
                  date_to: date | None, page: int, page_size: int) -> dict:
    conditions = []
    if customer_id is not None:
        conditions.append(CustomerPayment.customer_id == customer_id)
    if date_from:
        conditions.append(CustomerPayment.business_date >= date_from)
    if date_to:
        conditions.append(CustomerPayment.business_date <= date_to)
    total = db.scalar(select(func.count()).select_from(CustomerPayment).where(*conditions)) or 0
    payments = db.scalars(
        select(CustomerPayment).where(*conditions)
        .order_by(CustomerPayment.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [payment_out(db, p) for p in payments], "total": total, "page": page,
            "page_size": page_size}


def reverse_payment(db: Session, payment_id: int, reason: str, actor: User,
                    request: Request | None) -> CustomerPayment:
    """Undo a payment recorded by mistake (owner). The amount is owed again. Reversing twice
    changes nothing."""
    payment = db.get(CustomerPayment, payment_id, with_for_update=True, populate_existing=True)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found.")
    if payment.reversed:
        return payment
    db.get(Customer, payment.customer_id, with_for_update=True)  # same lock as taking payments
    sale_ids = [payment.sale_id] if payment.sale_id else list(db.scalars(
        select(PaymentAllocation.sale_id).where(PaymentAllocation.payment_id == payment.id)))
    blocked = db.scalar(select(Sale.receipt_no).where(
        Sale.id.in_(sale_ids),
        (Sale.status == SaleStatus.REVERSED) | Sale.id.in_(select(SaleReturn.sale_id)),
    ).limit(1))
    if blocked:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Receipt {blocked} has since been returned or reversed, and any refund "
                            "was worked out from this payment, so it can no longer be reversed.")
    payment.reversed = True
    payment.reversed_at = now_utc()
    payment.reversed_by_id = actor.id
    payment.reversal_reason = reason
    db.flush()
    audit.record(db, action="customer_payment.reversed", user=actor, entity_type="customer_payment",
                 entity_id=payment.id,
                 details={"amount": str(payment.amount), "customer_id": payment.customer_id,
                          "reason": reason},
                 request=request)
    db.commit()
    db.refresh(payment)
    return payment
