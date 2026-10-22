"""Quick sale: sell an item that is not in the catalogue yet, review it later (owner)."""
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.database import SessionLocal
from app.models import AuditLog, Product

IMEI = "356938035643809"


def cash(client):
    return client.get("/api/payment-methods").json()[0]["id"]


def quick_sale(client, name="Ring holder", price="1500", qty=1, key="q", paid=None, **extra):
    total = Decimal(price) * qty
    body = {"idempotency_key": key * 32,
            "lines": [{"quick_name": name, "unit_price": price, "quantity": qty}],
            "payments": [{"method_id": cash(client), "amount": str(paid or total)}]}
    body.update(extra)
    return client.post("/api/sales", json=body)


def pending(client):
    return client.get("/api/quick-sales").json()


def built_in_id():
    with SessionLocal() as db:
        return db.scalar(select(Product.id).where(Product.is_quick_sale.is_(True)))


def test_sell_an_item_not_in_the_catalogue(owner_client):
    r = quick_sale(owner_client, name="  Ring   holder ", qty=2)
    assert r.status_code == 201, r.text
    sale = r.json()
    line = sale["lines"][0]
    assert line["product_name"] == "Ring holder" and line["quick_name"] == "Ring holder"
    assert line["review_pending"] is True and sale["total"] == "3000.00"
    assert Decimal(sale["profit"]) == Decimal("3000")  # no cost yet
    rows = pending(owner_client)
    assert len(rows) == 1 and rows[0]["quick_name"] == "Ring holder" and rows[0]["quantity"] == 2
    assert owner_client.get("/api/dashboard").json()["quick_sales_to_review"] == 1
    # The built-in item never shows up in the catalogue or at the till.
    assert all(not p["name"].startswith("Quick sale")
               for p in owner_client.get("/api/products", params={"include_inactive": True}).json()["items"])
    assert owner_client.get("/api/pos/search", params={"q": "quick"}).json() == []


def test_quick_sale_rules(owner_client):
    def line(**kw):
        return owner_client.post("/api/sales", json={
            "idempotency_key": "r" * 32, "lines": [kw],
            "payments": [{"method_id": cash(owner_client), "amount": "100"}]})
    assert line(quick_name="Ring holder", quantity=1).status_code == 422  # no price
    assert line(quick_name="Ring holder", unit_price="100", unit_id=1).status_code == 422
    assert line(quick_name="Ring holder", product_id=5, unit_price="100").status_code == 422
    assert line(quantity=1).status_code == 422  # neither
    r = line(product_id=built_in_id(), unit_price="100")
    assert r.status_code == 422 and "Quick sale" in r.json()["detail"]
    bid = built_in_id()
    assert owner_client.patch(f"/api/products/{bid}", json={"name": "Mine"}).status_code == 409
    assert owner_client.delete(f"/api/products/{bid}").status_code == 409
    assert owner_client.post(f"/api/products/{bid}/cost",
                             json={"cost_each": "10", "reason": "x"}).status_code == 409


def test_review_by_entering_the_cost(owner_client):
    sale = quick_sale(owner_client, qty=2).json()
    lid = sale["lines"][0]["id"]
    r = owner_client.post(f"/api/quick-sales/{lid}/review", json={"cost_each": "600"})
    assert r.status_code == 200, r.text
    assert r.json()["linked_product_name"] is None and r.json()["cost_total"] == "1200.0000"
    again = owner_client.get(f"/api/sales/{sale['id']}").json()
    assert Decimal(again["profit"]) == Decimal("1800") and again["lines"][0]["review_pending"] is False
    assert pending(owner_client) == [] and owner_client.get("/api/dashboard").json()["quick_sales_to_review"] == 0
    assert owner_client.get("/api/quick-sales", params={"reviewed": True}).json()[0]["reviewed"] is True
    r = owner_client.post(f"/api/quick-sales/{lid}/review", json={"cost_each": "1"})
    assert r.status_code == 409
    with SessionLocal() as db:
        log = db.scalar(select(AuditLog).where(AuditLog.action == "sale.quick_reviewed"))
        assert log.details["item"] == "Ring holder" and log.details["cost_each"] == "600.00"


def test_review_by_linking_a_quantity_product(owner_client):
    sale = quick_sale(owner_client, name="Oraimo cable", price="3000", qty=2).json()
    lid = sale["lines"][0]["id"]
    pid = owner_client.post("/api/products", json={
        "name": "Oraimo Type-C cable", "tracking_type": "quantity", "selling_price": "3000"}).json()["id"]
    r = owner_client.post(f"/api/quick-sales/{lid}/review", json={"product_id": pid})
    assert r.status_code == 409 and "Add its stock first" in r.json()["detail"]
    owner_client.post(f"/api/products/{pid}/opening-stock", json={"quantity": 5, "unit_cost": "1800"})
    r = owner_client.post(f"/api/quick-sales/{lid}/review", json={"product_id": pid})
    assert r.status_code == 200, r.text
    assert r.json()["linked_product_name"] == "Oraimo Type-C cable"
    assert owner_client.get(f"/api/products/{pid}").json()["qty_on_hand"] == 3
    s = owner_client.get(f"/api/sales/{sale['id']}").json()
    assert s["lines"][0]["product_name"] == "Oraimo Type-C cable" and Decimal(s["profit"]) == Decimal("2400")
    best = owner_client.get("/api/dashboard").json()["best_sellers"]
    assert best[0]["name"] == "Oraimo Type-C cable" and best[0]["quantity"] == 2
    # Reversing the sale now puts the 2 cables back.
    r = owner_client.post(f"/api/sales/{sale['id']}/reverse",
                          json={"reason": "Entered twice", "refund_method_id": cash(owner_client)})
    assert r.status_code == 200, r.text
    assert owner_client.get(f"/api/products/{pid}").json()["qty_on_hand"] == 5


def test_review_by_linking_a_phone(owner_client):
    sale = quick_sale(owner_client, name="Tecno Spark 20", price="150000").json()
    lid = sale["lines"][0]["id"]
    pid = owner_client.post("/api/products", json={
        "name": "Tecno Spark 20", "tracking_type": "serialized", "selling_price": "150000"}).json()["id"]
    unit = owner_client.post(f"/api/products/{pid}/units/opening", json={
        "units": [{"imei1": IMEI, "unit_cost": "120000"}]}).json()
    unit_id = owner_client.get("/api/units", params={"q": IMEI}).json()["items"][0]["id"]
    assert unit is not None
    assert owner_client.post(f"/api/quick-sales/{lid}/review",
                             json={"product_id": pid}).status_code == 422  # which phone?
    r = owner_client.post(f"/api/quick-sales/{lid}/review", json={"product_id": pid, "unit_id": unit_id})
    assert r.status_code == 200, r.text
    s = owner_client.get(f"/api/sales/{sale['id']}").json()
    assert s["lines"][0]["imei1"] == IMEI and Decimal(s["profit"]) == Decimal("30000")
    assert owner_client.get("/api/units", params={"q": IMEI}).json()["items"][0]["status"] == "sold"
    # Several phones on one quick-sale line can only be given a cost.
    two = quick_sale(owner_client, name="Itel A70", price="70000", qty=2, key="w").json()
    r = owner_client.post(f"/api/quick-sales/{two['lines'][0]['id']}/review",
                          json={"product_id": pid, "unit_id": unit_id})
    assert r.status_code == 422 and "Enter what they cost" in r.json()["detail"]


def test_returned_or_reversed_quick_sales_need_no_review(owner_client):
    a = quick_sale(owner_client, key="a").json()
    b = quick_sale(owner_client, key="b").json()
    r = owner_client.post(f"/api/sales/{a['id']}/returns", json={
        "lines": [{"sale_line_id": a["lines"][0]["id"], "quantity": 1}], "reason": "Did not fit",
        "refund_method_id": cash(owner_client), "idempotency_key": "x" * 32})
    assert r.status_code == 201, r.text
    owner_client.post(f"/api/sales/{b['id']}/reverse",
                      json={"reason": "Mistake", "refund_method_id": cash(owner_client)})
    assert pending(owner_client) == []
    for s in (a, b):
        assert owner_client.post(f"/api/quick-sales/{s['lines'][0]['id']}/review",
                                 json={"cost_each": "1"}).status_code == 409


def test_staff_can_quick_sell_but_only_the_owner_reviews(owner_client, staff_client):
    sale = quick_sale(staff_client)
    assert sale.status_code == 201, sale.text
    assert sale.json()["profit"] is None
    assert staff_client.get("/api/quick-sales").status_code == 403
    lid = sale.json()["lines"][0]["id"]
    assert staff_client.post(f"/api/quick-sales/{lid}/review", json={"cost_each": "1"}).status_code == 403
    assert len(pending(owner_client)) == 1


def test_database_allows_only_one_review_of_quick_lines(owner_client):
    pid = owner_client.post("/api/products", json={
        "name": "Charger", "tracking_type": "quantity", "selling_price": "500",
        "opening_quantity": 3, "opening_unit_cost": "100"}).json()["id"]
    normal = owner_client.post("/api/sales", json={
        "idempotency_key": "n" * 32, "lines": [{"product_id": pid, "quantity": 1}],
        "payments": [{"method_id": cash(owner_client), "amount": "500"}]}).json()
    quick = quick_sale(owner_client).json()
    owner_client.post(f"/api/quick-sales/{quick['lines'][0]['id']}/review", json={"cost_each": "5"})
    for line_id in (normal["lines"][0]["id"], quick["lines"][0]["id"]):
        with SessionLocal() as db, pytest.raises(Exception, match="cannot be changed"):
            db.execute(text("UPDATE sale_lines SET unit_cost_snapshot = 0, reviewed_at = now() "
                            "WHERE id = :i"), {"i": line_id})
    with SessionLocal() as db, pytest.raises(Exception, match="cannot be changed"):
        db.execute(text("DELETE FROM sale_lines WHERE id = :i"), {"i": quick["lines"][0]["id"]})
