"""Stock ledger: opening stock, weighted-average cost and the database guards."""
import threading
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database import SessionLocal, engine
from app.models import MovementType
from app.services import stock as stock_service
from tests.conftest import logged_in
from tests.test_products import make_product, opening


def test_opening_stock_sets_quantity_cost_and_ledger(owner, owner_client):
    p = make_product(owner_client)
    r = owner_client.post(f"/api/products/{p['id']}/opening-stock",
                          json={"quantity": 12, "unit_cost": "4250.5", "note": " Shelf count "})
    assert r.status_code == 201, r.text
    assert r.json()["qty_on_hand"] == 12 and r.json()["avg_cost"] == "4250.5000"
    assert r.json()["opening_stock_open"] is False  # quantity items: once only

    moves = owner_client.get(f"/api/products/{p['id']}/movements").json()
    assert moves["total"] == 1
    m = moves["items"][0]
    assert m["movement_type"] == "opening" and m["quantity_change"] == 12
    assert m["balance_after"] == 12 and m["unit_cost"] == "4250.5000"
    assert m["reason"] == "Shelf count" and m["user_name"] == "Jalal Owner"
    assert m["product_sku"] == p["sku"]

    log = owner_client.get("/api/audit-logs", params={"action": "stock"}).json()["items"][0]
    assert log["action"] == "stock.opening"
    assert log["details"]["quantity"] == 12 and log["details"]["unit_cost"] == "4250.5000"


def test_opening_stock_rules(owner_client):
    p = make_product(owner_client)
    opening(owner_client, p["id"], 3)
    r = owner_client.post(f"/api/products/{p['id']}/opening-stock",
                          json={"quantity": 3, "unit_cost": "4000"})
    assert r.status_code == 409 and "only be entered once" in r.json()["detail"]

    phone = make_product(owner_client, name="iPhone 12", tracking_type="serialized",
                         selling_price="450000")
    r = owner_client.post(f"/api/products/{phone['id']}/opening-stock",
                          json={"quantity": 1, "unit_cost": "400000"})
    assert r.status_code == 409 and "unit by unit" in r.json()["detail"]

    off = make_product(owner_client, name="Old cable")
    owner_client.patch(f"/api/products/{off['id']}", json={"is_active": False})
    r = owner_client.post(f"/api/products/{off['id']}/opening-stock",
                          json={"quantity": 1, "unit_cost": "100"})
    assert r.status_code == 409 and "turned off" in r.json()["detail"]

    for bad in ({"quantity": 0, "unit_cost": "1"}, {"quantity": 1, "unit_cost": "-1"},
                {"quantity": 1.5, "unit_cost": "1"}, {"quantity": 1, "unit_cost": "1.23456"}):
        r = owner_client.post(f"/api/products/{off['id']}/opening-stock", json=bad)
        assert r.status_code == 422, bad
    r = owner_client.post("/api/products/999/opening-stock", json={"quantity": 1, "unit_cost": "1"})
    assert r.status_code == 404


def test_double_submitted_opening_stock_is_recorded_once(owner, owner_client):
    """Two simultaneous submissions (double click, two tabs): the row lock lets only one win."""
    p = make_product(owner_client)
    clients = [logged_in("owner"), logged_in("owner")]
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def submit(c):
        barrier.wait()
        codes.append(c.post(f"/api/products/{p['id']}/opening-stock",
                            json={"quantity": 7, "unit_cost": "1000"}).status_code)

    threads = [threading.Thread(target=submit, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(codes) == [201, 409]
    assert owner_client.get(f"/api/products/{p['id']}").json()["qty_on_hand"] == 7
    assert owner_client.get(f"/api/products/{p['id']}/movements").json()["total"] == 1


def test_weighted_average_cost():
    # Business rule 9: (10 × 100 + 5 × 130) ÷ 15 = 110
    assert stock_service.weighted_average(10, Decimal("100"), 5, Decimal("130")) == Decimal("110.0000")
    assert stock_service.weighted_average(0, Decimal("0"), 4, Decimal("250.5")) == Decimal("250.5000")
    # (1 × 1 + 2 × 2) ÷ 3 = 1.66666… → 1.6667
    assert stock_service.weighted_average(1, Decimal("1"), 2, Decimal("2")) == Decimal("1.6667")


def test_record_movement_receives_issues_and_refuses_overselling(owner, owner_client):
    p = make_product(owner_client)
    opening(owner_client, p["id"], 10, "100")

    with SessionLocal() as db:
        product = stock_service.lock_product(db, p["id"])
        stock_service.record_movement(db, product, quantity_change=5,
                                      movement_type=MovementType.PURCHASE, user=owner,
                                      unit_cost=Decimal("130"), reference_type="purchase",
                                      reference_id=1)
        out = stock_service.record_movement(db, product, quantity_change=-4,
                                            movement_type=MovementType.SALE, user=owner)
        db.commit()
        assert product.qty_on_hand == 11 and product.avg_cost == Decimal("110.0000")
        assert out.unit_cost == Decimal("110.0000")  # stock out valued at average cost
        assert out.balance_after == 11

    with SessionLocal() as db:
        product = stock_service.lock_product(db, p["id"])
        with pytest.raises(HTTPException) as exc:
            stock_service.record_movement(db, product, quantity_change=-12,
                                          movement_type=MovementType.SALE, user=owner)
        assert exc.value.status_code == 409 and "11 available, 12 needed" in exc.value.detail
        db.rollback()

    with SessionLocal() as db:
        product = stock_service.lock_product(db, p["id"])
        with pytest.raises(ValueError):  # stock received must carry a cost
            stock_service.record_movement(db, product, quantity_change=1,
                                          movement_type=MovementType.PURCHASE, user=owner)
        db.rollback()
        assert stock_service.ledger_mismatches(db) == []

    types = [m["movement_type"] for m in
             owner_client.get("/api/stock-movements", params={"product_id": p["id"]}).json()["items"]]
    assert types == ["sale", "purchase", "opening"]  # newest first
    sales = owner_client.get("/api/stock-movements", params={"movement_type": "sale"}).json()
    assert sales["total"] == 1


def test_database_refuses_quantity_changes_outside_the_ledger(owner_client):
    p = make_product(owner_client)
    opening(owner_client, p["id"], 5)

    # Changing the cached quantity directly is refused at commit
    with pytest.raises(IntegrityError, match="stock movements add up to 5"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE products SET qty_on_hand = 50 WHERE id = :id"), {"id": p["id"]})

    # So is a movement that the product's quantity does not reflect
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO stock_movements (product_id, quantity_change, balance_after, "
                "movement_type) VALUES (:id, 3, 8, 'adjustment')"), {"id": p["id"]})

    # And a product cannot be created with stock already on it
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO products (sku, name, tracking_type, selling_price, avg_cost, "
                "qty_on_hand, is_active) VALUES ('X1', 'X', 'quantity', 1, 0, 9, true)"))

    # Negative stock is refused even if the ledger agreed
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE products SET qty_on_hand = -1 WHERE id = :id"), {"id": p["id"]})

    assert owner_client.get(f"/api/products/{p['id']}").json()["qty_on_hand"] == 5


@pytest.mark.parametrize("sql", [
    "UPDATE stock_movements SET quantity_change = 99",
    "DELETE FROM stock_movements",
])
def test_stock_ledger_is_append_only(owner_client, sql):
    p = make_product(owner_client)
    opening(owner_client, p["id"], 5)
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(sql))
    assert owner_client.get(f"/api/products/{p['id']}/movements").json()["items"][0][
        "quantity_change"] == 5


def test_movement_endpoints(owner_client, staff_client):
    assert owner_client.get("/api/products/999/movements").status_code == 404
    assert staff_client.get("/api/stock-movements").status_code == 403
    assert owner_client.get("/api/stock-movements",
                            params={"movement_type": "teleport"}).status_code == 422
    assert owner_client.get("/api/stock-movements").json() == {
        "items": [], "total": 0, "page": 1, "page_size": 50}
