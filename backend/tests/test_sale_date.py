"""Entering an old sale on the day it really happened (owner only)."""
from datetime import timedelta

from sqlalchemy import select

from app.core.clock import business_today
from app.database import SessionLocal
from app.models import AuditLog


def _shop(client):
    r = client.post("/api/products", json={
        "name": "Oraimo 20W charger", "tracking_type": "quantity", "selling_price": "8500",
        "opening_quantity": 10, "opening_unit_cost": "5200"})
    assert r.status_code == 201, r.text
    return r.json()["id"], client.get("/api/payment-methods").json()[0]["id"]


def _sell(client, pid, cash, key, **extra):
    body = {"idempotency_key": key * 32, "lines": [{"product_id": pid, "quantity": 1}],
            "payments": [{"method_id": cash, "amount": "8500"}]}
    body.update(extra)
    return client.post("/api/sales", json=body)


def test_owner_records_a_sale_on_an_earlier_day(owner_client):
    pid, cash = _shop(owner_client)
    day = business_today() - timedelta(days=20)
    r = _sell(owner_client, pid, cash, "a", sale_date=day.isoformat())
    assert r.status_code == 201, r.text
    sale = r.json()
    assert sale["business_date"] == day.isoformat()
    assert sale["sold_at"].startswith(day.isoformat())
    assert sale["receipt_no"] == f"JDT-{day:%y%m%d}-0001"
    # Second old sale that day gets the next number; today's numbering is separate.
    assert _sell(owner_client, pid, cash, "b", sale_date=day.isoformat()).json()["receipt_no"] \
        == f"JDT-{day:%y%m%d}-0002"
    assert _sell(owner_client, pid, cash, "c").json()["receipt_no"] \
        == f"JDT-{business_today():%y%m%d}-0001"
    # Counts in that day's figures, not today's; stock still goes down.
    d = day.isoformat()
    period = owner_client.get("/api/dashboard", params={"date_from": d, "date_to": d}).json()["period"]
    assert period["sales_count"] == 2 and period["net_sales"] == "17000.00"
    t = business_today().isoformat()
    assert owner_client.get("/api/dashboard", params={"date_from": t, "date_to": t}).json()["period"]["sales_count"] == 1
    assert owner_client.get(f"/api/products/{pid}").json()["qty_on_hand"] == 7
    hist = owner_client.get("/api/sales", params={"date_from": d, "date_to": d}).json()["items"]
    assert len(hist) == 2
    with SessionLocal() as db:
        log = db.scalar(select(AuditLog).where(AuditLog.action == "sale.completed",
                                               AuditLog.entity_id == str(sale["id"])))
        assert log.details["sale_date"] == d and log.details["entered_late"] is True


def test_sale_date_rules(owner_client, staff_client):
    pid, cash = _shop(owner_client)
    tomorrow = (business_today() + timedelta(days=1)).isoformat()
    r = _sell(owner_client, pid, cash, "d", sale_date=tomorrow)
    assert r.status_code == 422 and "future" in r.json()["detail"]
    r = _sell(owner_client, pid, cash, "e", sale_date="1999-12-31")
    assert r.status_code == 422
    old = (business_today() - timedelta(days=3)).isoformat()
    r = _sell(staff_client, pid, cash, "f", sale_date=old)
    assert r.status_code == 403 and "owner" in r.json()["detail"]
    # Staff may still send today's date (same as leaving it empty).
    r = _sell(staff_client, pid, cash, "g", sale_date=business_today().isoformat())
    assert r.status_code == 201, r.text


def test_old_credit_sale_due_date_follows_the_sale_date(owner_client):
    pid, _ = _shop(owner_client)
    cust = owner_client.post("/api/customers", json={"name": "Aisha Bello", "phone": "08031234567"}).json()
    day = business_today() - timedelta(days=30)
    body = {"idempotency_key": "h" * 32, "customer_id": cust["id"],
            "lines": [{"product_id": pid, "quantity": 1}], "payments": [],
            "sale_date": day.isoformat()}
    r = owner_client.post("/api/sales", json={**body, "due_date": (day - timedelta(days=1)).isoformat()})
    assert r.status_code == 422 and "before the sale date" in r.json()["detail"]
    r = owner_client.post("/api/sales", json={**body, "due_date": (day + timedelta(days=7)).isoformat()})
    assert r.status_code == 201, r.text
    assert owner_client.get(f"/api/customers/{cust['id']}").json()["owed"] == "8500.00"
