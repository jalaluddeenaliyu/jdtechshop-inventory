"""Adding a product together with the stock already on the shelf (one step for the owner)."""
from decimal import Decimal

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import AuditLog, StockMovement


def _new(owner_client, **extra):
    body = {"name": "USB-C cable 1m", "tracking_type": "quantity", "selling_price": "3500"}
    body.update(extra)
    return owner_client.post("/api/products", json=body)


def test_product_with_opening_stock_is_saved_with_quantity_and_cost(owner_client):
    r = _new(owner_client, opening_quantity=12, opening_unit_cost="2000")
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["qty_on_hand"] == 12
    assert Decimal(p["avg_cost"]) == Decimal("2000")
    # Opening stock is now used, so the separate button no longer appears.
    assert p["opening_stock_open"] is False
    with SessionLocal() as db:
        moves = db.scalars(select(StockMovement).where(StockMovement.product_id == p["id"])).all()
        assert [(m.movement_type, m.quantity_change) for m in moves] == [("opening", 12)]
        actions = set(db.scalars(select(AuditLog.action)))
        assert {"product.created", "stock.opening"} <= actions


def test_product_without_opening_stock_still_works(owner_client):
    r = _new(owner_client)
    assert r.status_code == 201, r.text
    assert r.json()["qty_on_hand"] == 0 and r.json()["opening_stock_open"] is True


def test_blank_opening_fields_mean_no_stock(owner_client):
    r = _new(owner_client, opening_quantity="", opening_unit_cost="")
    assert r.status_code == 201, r.text
    assert r.json()["qty_on_hand"] == 0


def test_quantity_without_cost_is_refused_and_nothing_is_saved(owner_client):
    r = _new(owner_client, opening_quantity=5)
    assert r.status_code == 422
    assert "cost" in r.json()["detail"]
    r = _new(owner_client, opening_unit_cost="100")
    assert r.status_code == 422
    assert owner_client.get("/api/products").json()["total"] == 0


def test_serialized_products_cannot_take_a_bare_quantity(owner_client):
    r = owner_client.post("/api/products", json={
        "name": "iPhone 16 128GB", "tracking_type": "serialized", "selling_price": "1200000",
        "opening_quantity": 3, "opening_unit_cost": "1000000"})
    assert r.status_code == 422
    assert "IMEI" in r.json()["detail"]


def test_bad_opening_values_refused(owner_client):
    assert _new(owner_client, opening_quantity=0, opening_unit_cost="10").status_code == 422
    assert _new(owner_client, opening_quantity=2, opening_unit_cost="-1").status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(StockMovement)) == 0


def test_staff_cannot_create_products_with_stock(staff_client):
    assert _new(staff_client, opening_quantity=3, opening_unit_cost="10").status_code == 403


def test_stock_value_and_cost_each_for_owner_only(owner_client, staff_client):
    pid = _new(owner_client, opening_quantity=4, opening_unit_cost="2500").json()["id"]
    p = owner_client.get(f"/api/products/{pid}").json()
    assert Decimal(p["stock_value"]) == Decimal("10000.00")
    assert Decimal(p["cost_each"]) == Decimal("2500.00")
    listed = owner_client.get("/api/products").json()["items"][0]
    assert Decimal(listed["stock_value"]) == Decimal("10000.00")
    s = staff_client.get(f"/api/products/{pid}").json()
    assert s["stock_value"] is None and s["cost_each"] is None and s["avg_cost"] is None


def test_phone_stock_value_uses_each_units_own_cost(owner_client):
    pid = owner_client.post("/api/products", json={
        "name": "iPhone 16 128GB", "tracking_type": "serialized", "selling_price": "1300000",
    }).json()["id"]
    r = owner_client.post(f"/api/products/{pid}/units/opening", json={"units": [
        {"imei1": "356938035643809", "unit_cost": "1000000", "condition": "new"},
        {"imei1": "490154203237518", "unit_cost": "1100000", "condition": "new"},
    ]})
    assert r.status_code in (200, 201), r.text
    p = owner_client.get(f"/api/products/{pid}").json()
    assert p["qty_on_hand"] == 2
    assert Decimal(p["stock_value"]) == Decimal("2100000.00")
    assert Decimal(p["cost_each"]) == Decimal("1050000.00")


def test_duplicate_product_name_is_refused_with_a_helpful_message(owner_client):
    first = _new(owner_client)
    assert first.status_code == 201
    r = _new(owner_client, name="  usb-c   CABLE 1m ")
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"] and first.json()["sku"] in r.json()["detail"]
    other = _new(owner_client, name="USB-C cable 2m").json()
    r = owner_client.patch(f"/api/products/{other['id']}", json={"name": "USB-C Cable 1m"})
    assert r.status_code == 409
    # Saving a product under its own name is fine.
    r = owner_client.patch(f"/api/products/{other['id']}", json={"name": "USB-C cable 2m",
                                                                  "selling_price": "4000"})
    assert r.status_code == 200, r.text
