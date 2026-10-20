"""Correcting a wrongly entered cost price."""
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditLog, SaleLine

IMEIS = ["356938035643809", "490154203237518"]


def _product(client, **extra):
    body = {"name": "mi120", "tracking_type": "quantity", "selling_price": "10000"}
    body.update(extra)
    r = client.post("/api/products", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sell(client, product_id, n, key, unit_id=None, price="10000"):
    cash = client.get("/api/payment-methods").json()[0]["id"]
    line = {"product_id": product_id, "quantity": n}
    if unit_id:
        line["unit_id"] = unit_id
    r = client.post("/api/sales", json={"idempotency_key": key * 32, "lines": [line],
                                        "payments": [{"method_id": cash,
                                                      "amount": str(Decimal(price) * n)}]})
    assert r.status_code == 201, r.text
    return r.json()


def test_wrong_cost_is_corrected_for_stock_on_hand(owner_client):
    p = _product(owner_client, opening_quantity=200, opening_unit_cost="5000")
    r = owner_client.post(f"/api/products/{p['id']}/cost",
                          json={"cost_each": "₦500", "reason": "Typed 5000, it was 500"})
    assert r.status_code == 200, r.text
    after = r.json()
    assert Decimal(after["cost_each"]) == Decimal("500.00")
    assert Decimal(after["stock_value"]) == Decimal("100000.00")
    with SessionLocal() as db:
        log = db.scalars(select(AuditLog).where(AuditLog.action == "product.cost_corrected")).one()
    assert log.details["cost_from"] == "5000.00" and log.details["cost_to"] == "500.00"
    assert log.details["value_change"] == "-900000.00"
    assert log.details["reason"] == "Typed 5000, it was 500"


def test_past_sales_keep_their_cost_and_future_sales_use_the_new_one(owner_client):
    p = _product(owner_client, opening_quantity=10, opening_unit_cost="5000")
    _sell(owner_client, p["id"], 1, "a")
    owner_client.post(f"/api/products/{p['id']}/cost", json={"cost_each": "6000"})
    _sell(owner_client, p["id"], 1, "b")
    with SessionLocal() as db:
        costs = list(db.scalars(select(SaleLine.unit_cost_snapshot).order_by(SaleLine.id)))
    assert [Decimal(c) for c in costs] == [Decimal("5000"), Decimal("6000")]


def test_phone_costs_corrected_all_at_once_or_one_by_one(owner_client):
    p = _product(owner_client, name="iPhone 16", tracking_type="serialized", selling_price="790000")
    r = owner_client.post(f"/api/products/{p['id']}/units/opening", json={"units": [
        {"imei1": IMEIS[0], "unit_cost": "700000"}, {"imei1": IMEIS[1], "unit_cost": "710000"}]})
    units = r.json()["units"]
    r = owner_client.post(f"/api/products/{p['id']}/cost", json={"cost_each": "650000"})
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["stock_value"]) == Decimal("1300000.00")
    r = owner_client.post(f"/api/products/{p['id']}/cost",
                          json={"cost_each": "660000", "unit_id": units[1]["id"]})
    assert Decimal(r.json()["stock_value"]) == Decimal("1310000.00")
    assert Decimal(r.json()["cost_each"]) == Decimal("655000.00")


def test_a_sold_phone_keeps_its_cost(owner_client):
    p = _product(owner_client, name="iPhone 16", tracking_type="serialized", selling_price="790000")
    units = owner_client.post(f"/api/products/{p['id']}/units/opening", json={"units": [
        {"imei1": IMEIS[0], "unit_cost": "700000"}]}).json()["units"]
    _sell(owner_client, p["id"], 1, "c", unit_id=units[0]["id"], price="790000")
    r = owner_client.post(f"/api/products/{p['id']}/cost",
                          json={"cost_each": "1", "unit_id": units[0]["id"]})
    assert r.status_code == 409 and "sold" in r.json()["detail"]
    r = owner_client.post(f"/api/products/{p['id']}/cost", json={"cost_each": "1"})
    assert r.status_code == 409


def test_same_cost_changes_nothing_and_staff_are_refused(owner_client, staff_client):
    p = _product(owner_client, opening_quantity=3, opening_unit_cost="5000")
    r = owner_client.post(f"/api/products/{p['id']}/cost", json={"cost_each": "5000.00"})
    assert r.status_code == 200
    with SessionLocal() as db:
        assert db.scalars(select(AuditLog).where(
            AuditLog.action == "product.cost_corrected")).first() is None
    assert staff_client.post(f"/api/products/{p['id']}/cost",
                             json={"cost_each": "1"}).status_code == 403
    assert owner_client.post(f"/api/products/{p['id']}/cost",
                             json={"cost_each": "-5"}).status_code == 422
