"""Customer debts and repayments: calculated balances, allocation, no overpayment."""
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import business_today
from app.database import engine
from tests.conftest import logged_in
from tests.test_products import make_product, opening

TODAY = business_today()


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def shop(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="10000")
    opening(owner_client, charger["id"], 50, "6000")
    aisha = staff_client.post("/api/customers", json={"name": "Aisha Bello", "phone": "08031234567"}).json()
    return {"charger": charger, "aisha": aisha}


def credit_sale(client, shop, quantity: int, paid: str | None = None, **extra) -> dict:
    r = client.post("/api/sales", json={
        "customer_id": shop["aisha"]["id"], "lines": [{"product_id": shop["charger"]["id"], "quantity": quantity}],
        "payments": [{"method_id": 1, "amount": paid}] if paid else [], "idempotency_key": key(), **extra})
    assert r.status_code == 201, r.text
    return r.json()


def pay(client, customer_id: int, amount: str, **extra):
    return client.post("/api/customer-payments", json={
        "customer_id": customer_id, "amount": amount, "method_id": extra.pop("method_id", 1),
        "idempotency_key": extra.pop("idempotency_key", key()), **extra})


def owed(client, customer_id: int) -> str:
    return client.get(f"/api/customers/{customer_id}").json()["owed"]


def test_balance_is_calculated_from_sales_and_payments(staff_client, shop):
    aisha = shop["aisha"]["id"]
    assert owed(staff_client, aisha) == "0.00"
    s1 = credit_sale(staff_client, shop, 2, "5000")   # 20,000 − 5,000 = 15,000 owed
    s2 = credit_sale(staff_client, shop, 1)            # 10,000 owed
    assert owed(staff_client, aisha) == "25000.00"

    debt = staff_client.get(f"/api/customers/{aisha}/debt").json()
    assert debt["sales_total"] == "30000.00" and debt["paid_total"] == "5000.00"
    assert [(s["receipt_no"], s["balance"]) for s in debt["unpaid_sales"]] == [
        (s1["receipt_no"], "15000.00"), (s2["receipt_no"], "10000.00")]  # oldest first

    # A cash customer never shows up as owing
    bola = staff_client.post("/api/customers", json={"name": "Bola"}).json()
    staff_client.post("/api/sales", json={"customer_id": bola["id"], "idempotency_key": key(),
                                          "lines": [{"product_id": shop["charger"]["id"]}],
                                          "payments": [{"method_id": 1, "amount": "10000"}]})
    owing = staff_client.get("/api/customers", params={"owing_only": True}).json()["items"]
    assert [(c["name"], c["owed"]) for c in owing] == [("Aisha Bello", "25000.00")]
    assert staff_client.get(f"/api/customers/{bola['id']}/debt").json()["unpaid_sales"] == []


def test_paying_one_sale(owner_client, staff_client, shop):
    aisha = shop["aisha"]["id"]
    s1 = credit_sale(staff_client, shop, 2, "5000")
    credit_sale(staff_client, shop, 1)

    r = pay(staff_client, aisha, "15000.01", sale_id=s1["id"])
    assert r.status_code == 409 and f"receipt {s1['receipt_no']} (₦15,000.00)" in r.json()["detail"]

    r = pay(staff_client, aisha, "15000", sale_id=s1["id"], reference="TRF-12")
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["reference_no"] == f"PAY-{p['id']:06d}" and p["at_till"] is False
    assert p["allocations"] == [{"sale_id": s1["id"], "receipt_no": s1["receipt_no"], "amount": "15000.00"}]
    assert p["customer_owes_now"] == "10000.00" and p["recorded_by_name"] == "Amina Cashier"
    assert p["received_on"] == TODAY.isoformat()

    sale = staff_client.get(f"/api/sales/{s1['id']}").json()
    assert sale["balance_due"] == "0.00" and sale["amount_paid"] == "20000.00"
    assert [x["amount"] for x in sale["payments"]] == ["5000.00", "15000.00"]
    unpaid = staff_client.get("/api/sales", params={"unpaid_only": True}).json()["items"]
    assert [s["id"] for s in unpaid] != [s1["id"]] and len(unpaid) == 1

    r = pay(staff_client, aisha, "1", sale_id=s1["id"])
    assert r.status_code == 409 and "already fully paid" in r.json()["detail"]

    log = owner_client.get("/api/audit-logs", params={"action": "customer_payment"}).json()["items"]
    assert log[0]["details"]["allocations"] == [f"{s1['receipt_no']}: 15000.00"]


def test_paying_towards_the_balance_clears_oldest_sales_first(staff_client, shop):
    aisha = shop["aisha"]["id"]
    s1 = credit_sale(staff_client, shop, 1)  # 10,000
    s2 = credit_sale(staff_client, shop, 2)  # 20,000
    s3 = credit_sale(staff_client, shop, 1)  # 10,000
    r = pay(staff_client, aisha, "25000")
    assert r.status_code == 201
    assert [(a["receipt_no"], a["amount"]) for a in r.json()["allocations"]] == [
        (s1["receipt_no"], "10000.00"), (s2["receipt_no"], "15000.00")]
    assert r.json()["customer_owes_now"] == "15000.00"
    balances = {s["receipt_no"]: s["balance"] for s in
                staff_client.get(f"/api/customers/{aisha}/debt").json()["unpaid_sales"]}
    assert balances == {s2["receipt_no"]: "5000.00", s3["receipt_no"]: "10000.00"}


def test_no_overpayment(staff_client, shop):
    aisha = shop["aisha"]["id"]
    credit_sale(staff_client, shop, 1, "4000")  # owes 6,000
    r = pay(staff_client, aisha, "6000.01")
    assert r.status_code == 409 and "Aisha Bello owes (₦6,000.00)" in r.json()["detail"]
    assert pay(staff_client, aisha, "6000").status_code == 201
    assert owed(staff_client, aisha) == "0.00"
    r = pay(staff_client, aisha, "0.01")
    assert r.status_code == 409 and "Overpayments are not allowed" in r.json()["detail"]


def test_payment_validation(owner_client, staff_client, shop):
    aisha = shop["aisha"]["id"]
    sale = credit_sale(staff_client, shop, 1)
    walk_in = staff_client.get("/api/customers/walk-in").json()
    other = staff_client.post("/api/customers", json={"name": "Other"}).json()

    r = pay(staff_client, walk_in["id"], "10")
    assert r.status_code == 422 and "Walk-in" in r.json()["detail"]
    assert pay(staff_client, other["id"], "10", sale_id=sale["id"]).status_code == 422
    assert pay(staff_client, 999, "10").status_code == 422
    assert pay(staff_client, aisha, "0").status_code == 422
    assert pay(staff_client, aisha, "1.005").status_code == 422
    r = pay(staff_client, aisha, "10", received_on=(TODAY + timedelta(days=1)).isoformat())
    assert r.status_code == 422 and "future" in r.json()["detail"]
    owner_client.patch("/api/payment-methods/2", json={"is_active": False})
    assert pay(staff_client, aisha, "10", method_id=2).status_code == 422

    # A turned-off customer can still pay what they owe; an earlier date is fine
    owner_client.patch(f"/api/customers/{aisha}", json={"is_active": False})
    r = pay(staff_client, aisha, "100", received_on=(TODAY - timedelta(days=2)).isoformat())
    assert r.status_code == 201 and r.json()["received_on"] == (TODAY - timedelta(days=2)).isoformat()


def test_same_payment_form_twice_records_once(staff_client, shop):
    aisha = shop["aisha"]["id"]
    credit_sale(staff_client, shop, 1)
    k = key()
    first = pay(staff_client, aisha, "4000", idempotency_key=k)
    again = pay(staff_client, aisha, "4000", idempotency_key=k)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert owed(staff_client, aisha) == "6000.00"
    r = pay(staff_client, aisha, "100", idempotency_key=k)
    assert r.status_code == 409 and "already used" in r.json()["detail"]


def test_two_cashiers_cannot_both_take_the_last_payment(staff_client, shop, owner):
    aisha = shop["aisha"]["id"]
    credit_sale(staff_client, shop, 1)  # owes 10,000
    clients = [logged_in("cashier"), logged_in("cashier")]
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def go(c):
        barrier.wait()
        codes.append(pay(c, aisha, "10000").status_code)

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201, 409]
    assert owed(staff_client, aisha) == "0.00"


def test_overdue_debts(staff_client, shop, monkeypatch):
    aisha = shop["aisha"]["id"]
    # A credit sale made 10 days ago, due 3 days ago…
    past = TODAY - timedelta(days=10)
    monkeypatch.setattr("app.services.sales.business_today", lambda: past)
    old = credit_sale(staff_client, shop, 2, due_date=(TODAY - timedelta(days=3)).isoformat())
    monkeypatch.undo()
    # …and one due next week
    credit_sale(staff_client, shop, 1, due_date=(TODAY + timedelta(days=7)).isoformat())

    customer = staff_client.get(f"/api/customers/{aisha}").json()
    assert customer["owed"] == "30000.00" and customer["overdue"] == "20000.00"
    debt = staff_client.get(f"/api/customers/{aisha}/debt").json()
    assert [s["overdue"] for s in debt["unpaid_sales"]] == [True, False]
    assert debt["unpaid_sales"][0]["receipt_no"] == old["receipt_no"]

    pay(staff_client, aisha, "20000")  # pays the oldest (overdue) sale first
    assert staff_client.get(f"/api/customers/{aisha}").json()["overdue"] == "0.00"


def test_payment_history_and_receipt(staff_client, shop):
    aisha = shop["aisha"]["id"]
    credit_sale(staff_client, shop, 1, "2000")
    p = pay(staff_client, aisha, "3000").json()
    items = staff_client.get("/api/customer-payments", params={"customer_id": aisha}).json()["items"]
    assert [(i["amount"], i["at_till"]) for i in items] == [("3000.00", False), ("2000.00", True)]
    assert staff_client.get("/api/customer-payments",
                            params={"date_to": (TODAY - timedelta(days=1)).isoformat()}).json()["total"] == 0
    assert staff_client.get(f"/api/customer-payments/{p['id']}").json()["customer_name"] == "Aisha Bello"
    assert staff_client.get("/api/customer-payments/999").status_code == 404


def test_allocations_are_permanent(staff_client, shop):
    credit_sale(staff_client, shop, 1)
    pay(staff_client, shop["aisha"]["id"], "100")
    for sql in ("UPDATE customer_payment_allocations SET amount = 1",
                "DELETE FROM customer_payment_allocations"):
        with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
            with engine.begin() as conn:
                conn.execute(text(sql))


def test_payments_need_login(client):
    assert client.post("/api/customer-payments", json={}).status_code == 401
    assert client.get("/api/customer-payments").status_code == 401
    assert client.get("/api/customers/1/debt").status_code == 401
