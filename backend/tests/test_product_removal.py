"""Deleting products added by mistake, without ever erasing real history."""
from decimal import Decimal

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import AuditLog, Product, StockMovement

IMEIS = ["356938035643809", "490154203237518"]


def _product(client, **extra):
    body = {"name": "Oraimo 20W charger", "tracking_type": "quantity", "selling_price": "8500"}
    body.update(extra)
    r = client.post("/api/products", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _actions():
    with SessionLocal() as db:
        return list(db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


def test_product_with_no_history_is_deleted_completely(owner_client):
    p = _product(owner_client)
    r = owner_client.delete(f"/api/products/{p['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "deleted"
    assert owner_client.get(f"/api/products/{p['id']}").status_code == 404
    assert "product.deleted" in _actions()


def test_wrong_opening_stock_is_taken_out_and_name_can_be_reused(owner_client):
    p = _product(owner_client, opening_quantity=200, opening_unit_cost="5000")
    r = owner_client.delete(f"/api/products/{p['id']}", params={"reason": "Typed 200, meant 20"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "removed" and body["quantity_removed"] == 200
    assert Decimal(body["value_removed"]) == Decimal("1000000.00")
    gone = owner_client.get(f"/api/products/{p['id']}").json()
    assert gone["is_active"] is False and gone["qty_on_hand"] == 0
    with SessionLocal() as db:
        moves = db.execute(select(StockMovement.movement_type, StockMovement.quantity_change,
                                  StockMovement.reason)
                           .where(StockMovement.product_id == p["id"])
                           .order_by(StockMovement.id)).all()
    assert [(m[0], m[1]) for m in moves] == [("opening", 200), ("reversal", -200)]
    assert "Typed 200, meant 20" in moves[1][2]
    # The same name can be added again, correctly.
    again = _product(owner_client, opening_quantity=20, opening_unit_cost="5000")
    assert again["qty_on_hand"] == 20
    # ...and the removed one can't be turned back on while the new one exists.
    r = owner_client.patch(f"/api/products/{p['id']}", json={"is_active": True})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]


def test_phone_added_to_wrong_product_can_be_removed_and_re_added(owner_client):
    wrong = _product(owner_client, name="iPhone 16", tracking_type="serialized",
                     selling_price="790000")
    r = owner_client.post(f"/api/products/{wrong['id']}/units/opening", json={"units": [
        {"imei1": IMEIS[0], "unit_cost": "700000"}, {"imei1": IMEIS[1], "unit_cost": "710000"}]})
    assert r.status_code == 201, r.text
    r = owner_client.delete(f"/api/products/{wrong['id']}")
    assert r.status_code == 200 and r.json()["result"] == "removed"
    assert r.json()["quantity_removed"] == 2
    assert Decimal(r.json()["value_removed"]) == Decimal("1410000.00")
    # The same phones go onto the right product without "already registered" errors.
    right = _product(owner_client, name="iPhone 16 128GB", tracking_type="serialized",
                     selling_price="790000")
    r = owner_client.post(f"/api/products/{right['id']}/units/opening", json={"units": [
        {"imei1": IMEIS[0], "unit_cost": "700000"}, {"imei1": IMEIS[1], "unit_cost": "710000"}]})
    assert r.status_code == 201, r.text
    units = owner_client.get("/api/units", params={"q": IMEIS[0]}).json()["items"]
    assert [u["product_id"] for u in units if u["status"] == "in_stock"] == [right["id"]]


def test_sold_or_bought_products_cannot_be_deleted(owner_client):
    p = _product(owner_client, opening_quantity=5, opening_unit_cost="5000")
    cash = owner_client.get("/api/payment-methods").json()[0]["id"]
    r = owner_client.post("/api/sales", json={
        "idempotency_key": "k" * 32, "lines": [{"product_id": p["id"], "quantity": 1}],
        "payments": [{"method_id": cash, "amount": "8500"}]})
    assert r.status_code == 201, r.text
    r = owner_client.delete(f"/api/products/{p['id']}")
    assert r.status_code == 409 and "untick" in r.json()["detail"]
    assert owner_client.get(f"/api/products/{p['id']}").json()["qty_on_hand"] == 4


def test_product_on_a_draft_purchase_cannot_be_deleted(owner_client):
    p = _product(owner_client)
    s = owner_client.post("/api/suppliers", json={"name": "Computer Village"}).json()
    r = owner_client.post("/api/purchases", json={
        "supplier_id": s["id"], "purchase_date": "2026-10-01", "idempotency_key": "p" * 32,
        "lines": [{"product_id": p["id"], "quantity": 2, "unit_cost": "4000"}]})
    assert r.status_code == 201, r.text
    r = owner_client.delete(f"/api/products/{p['id']}")
    assert r.status_code == 409 and "purchase" in r.json()["detail"]


def test_staff_cannot_delete_products(owner_client, staff_client):
    p = _product(owner_client)
    assert staff_client.delete(f"/api/products/{p['id']}").status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Product)) == 1
