"""Services (sticker cutting, printing, repairs): sold at the till, no stock."""
from decimal import Decimal

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import SaleLine, StockMovement


def _service(client, **extra):
    body = {"name": "Phone sticker – full body", "tracking_type": "service",
            "selling_price": "3000", "service_cost": "800"}
    body.update(extra)
    r = client.post("/api/products", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _cash(client):
    return client.get("/api/payment-methods").json()[0]["id"]


def _sale(client, lines, paid, key):
    return client.post("/api/sales", json={"idempotency_key": key * 32, "lines": lines,
                                           "payments": [{"method_id": _cash(client),
                                                         "amount": str(paid)}]})


def test_service_has_no_stock_and_shows_cost_per_job(owner_client):
    s = _service(owner_client)
    assert s["tracking_type"] == "service" and s["qty_on_hand"] == 0
    assert s["is_low_stock"] is False and s["opening_stock_open"] is False
    assert s["condition"] is None
    assert Decimal(s["cost_each"]) == Decimal("800") and Decimal(s["stock_value"]) == 0


def test_selling_a_service_records_profit_without_touching_stock(owner_client):
    s = _service(owner_client)
    charger = owner_client.post("/api/products", json={
        "name": "Charger", "tracking_type": "quantity", "selling_price": "8500",
        "opening_quantity": 5, "opening_unit_cost": "5000"}).json()
    r = _sale(owner_client, [{"product_id": s["id"], "quantity": 2},
                             {"product_id": charger["id"], "quantity": 1}], 14500, "a")
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        moves = db.scalar(select(func.count()).select_from(StockMovement)
                          .where(StockMovement.product_id == s["id"]))
        line = db.scalars(select(SaleLine).where(SaleLine.product_id == s["id"])).one()
    assert moves == 0
    assert line.cost_total_snapshot == Decimal("1600") and line.line_total == Decimal("6000")
    assert owner_client.get(f"/api/products/{charger['id']}").json()["qty_on_hand"] == 4


def test_price_set_at_the_till(owner_client):
    s = _service(owner_client, name="Custom sticker printing", selling_price="0",
                 service_cost=None)
    r = _sale(owner_client, [{"product_id": s["id"]}], 4500, "b")
    assert r.status_code == 422 and "enter the price" in r.json()["detail"]
    r = _sale(owner_client, [{"product_id": s["id"], "unit_price": "4500"}], 4500, "c")
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "4500.00"


def test_fixed_price_cannot_be_overridden(owner_client, staff_client):
    s = _service(owner_client)
    r = _sale(staff_client, [{"product_id": s["id"], "unit_price": "100"}], 100, "d")
    assert r.status_code == 422 and "fixed" in r.json()["detail"]


def test_services_cannot_get_stock_purchases_or_adjustments(owner_client):
    s = _service(owner_client)
    r = owner_client.post(f"/api/products/{s['id']}/opening-stock",
                          json={"quantity": 5, "unit_cost": "100"})
    assert r.status_code == 409 and "service" in r.json()["detail"]
    sup = owner_client.post("/api/suppliers", json={"name": "Vinyl Ltd"}).json()
    r = owner_client.post("/api/purchases", json={
        "supplier_id": sup["id"], "purchase_date": "2026-10-01", "idempotency_key": "p" * 32,
        "lines": [{"product_id": s["id"], "quantity": 2, "unit_cost": "100"}]})
    assert r.status_code == 422 and "service" in r.json()["detail"]
    r = owner_client.post("/api/products", json={
        "name": "Bad", "tracking_type": "service", "selling_price": "1",
        "opening_quantity": 3, "opening_unit_cost": "1"})
    assert r.status_code == 422


def test_service_not_in_stock_reports_or_low_stock(owner_client):
    _service(owner_client)
    low = owner_client.get("/api/products", params={"stock": "low"}).json()
    assert low["total"] == 0
    stock = owner_client.get("/api/reports/stock").json()
    assert "Phone sticker" not in str(stock)


def test_returning_a_service_refunds_without_stock(owner_client):
    s = _service(owner_client)
    sale = _sale(owner_client, [{"product_id": s["id"]}], 3000, "e").json()
    line_id = sale["lines"][0]["id"]
    r = owner_client.post(f"/api/sales/{sale['id']}/returns", json={
        "reason": "Customer didn't like it", "idempotency_key": "r" * 32,
        "lines": [{"sale_line_id": line_id, "quantity": 1, "restock": True}],
        "refund_method_id": _cash(owner_client)})
    assert r.status_code in (200, 201), r.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(StockMovement)) == 0


def test_cost_per_job_can_be_corrected_and_type_is_locked_after_a_sale(owner_client):
    s = _service(owner_client)
    r = owner_client.post(f"/api/products/{s['id']}/cost", json={"cost_each": "1000"})
    assert r.status_code == 200 and Decimal(r.json()["cost_each"]) == Decimal("1000")
    _sale(owner_client, [{"product_id": s["id"]}], 3000, "f")
    r = owner_client.patch(f"/api/products/{s['id']}", json={"tracking_type": "quantity"})
    assert r.status_code == 409 and "service" in r.json()["detail"]
