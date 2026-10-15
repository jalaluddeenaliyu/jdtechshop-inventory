"""Stock adjustments: requested by anyone, applied only when the owner approves."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database import engine
from tests.conftest import logged_in, make_user
from tests.test_products import make_product, opening
from tests.test_units import imei, phone


@pytest.fixture
def stock(owner_client):
    charger = make_product(owner_client, name="Charger")
    opening(owner_client, charger["id"], 5, "4000")
    a54 = phone(owner_client, name="Galaxy A54")
    units = owner_client.post(f"/api/products/{a54['id']}/units/opening", json={"units": [
        {"imei1": imei(1), "unit_cost": "380000"}]}).json()["units"]
    return {"charger": charger, "phone": a54, "unit": units[0]}


def request_(client, lines, reason="Shelf count", **extra):
    return client.post("/api/stock-adjustments", json={"lines": lines, "reason": reason, **extra})


def qty(client, product_id) -> int:
    return client.get(f"/api/products/{product_id}").json()["qty_on_hand"]


def test_staff_request_waits_for_owner_approval(owner_client, staff_client, stock):
    charger = stock["charger"]["id"]
    r = request_(staff_client, [{"product_id": charger, "quantity_change": -2, "kind": "damage"}],
                 reason="Dropped two chargers")
    assert r.status_code == 201, r.text
    adj = r.json()
    assert adj["status"] == "pending" and adj["requested_by_name"] == "Amina Cashier"
    assert qty(owner_client, charger) == 5  # nothing moves until approved

    assert staff_client.post(f"/api/stock-adjustments/{adj['id']}/approve", json={}).status_code == 403
    r = owner_client.post(f"/api/stock-adjustments/{adj['id']}/approve", json={"note": "Seen the boxes"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decided_by_name"] == "Jalal Owner"
    assert qty(owner_client, charger) == 3
    m = owner_client.get("/api/stock-movements", params={"movement_type": "damage"}).json()["items"][0]
    assert m["quantity_change"] == -2 and m["reference_type"] == "adjustment" and m["reason"] == "Dropped two chargers"

    # Decided once only
    assert owner_client.post(f"/api/stock-adjustments/{adj['id']}/reject", json={}).status_code == 409
    with pytest.raises(DBAPIError, match="once decided"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE stock_adjustments SET reason = 'edited'"))


def test_rejecting_changes_nothing(owner_client, staff_client, stock):
    adj = request_(staff_client, [{"product_id": stock["charger"]["id"], "quantity_change": 3}]).json()
    r = owner_client.post(f"/api/stock-adjustments/{adj['id']}/reject", json={"note": "Recount first"})
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["decision_note"] == "Recount first"
    assert qty(owner_client, stock["charger"]["id"]) == 5


def test_units_go_missing_and_are_found(owner_client, staff_client, stock):
    unit, a54 = stock["unit"], stock["phone"]["id"]
    r = request_(owner_client, [{"product_id": a54, "unit_id": unit["id"], "unit_new_status": "missing"}],
                 reason="Not on the shelf", approve_now=True)
    assert r.status_code == 201 and r.json()["status"] == "approved"
    assert r.json()["lines"][0]["kind"] == "missing" and r.json()["lines"][0]["quantity_change"] == -1
    assert owner_client.get(f"/api/units/{unit['id']}").json()["status"] == "missing"
    assert qty(owner_client, a54) == 0

    r = request_(owner_client, [{"product_id": a54, "unit_id": unit["id"], "unit_new_status": "in_stock"}],
                 reason="Found in the drawer", approve_now=True)
    assert r.json()["lines"][0]["quantity_change"] == 1
    assert owner_client.get(f"/api/units/{unit['id']}").json()["status"] == "in_stock"
    assert qty(owner_client, a54) == 1
    # Staff cannot approve their own request
    r = request_(staff_client, [{"product_id": a54, "unit_id": unit["id"], "unit_new_status": "damaged"}],
                 approve_now=True)
    assert r.status_code == 403


def test_approval_rechecks_stock(owner_client, staff_client, stock):
    charger = stock["charger"]["id"]
    adj = request_(staff_client, [{"product_id": charger, "quantity_change": -4, "kind": "missing"}]).json()
    # Meanwhile two are sold
    r = staff_client.post("/api/sales", json={"lines": [{"product_id": charger, "quantity": 2}],
                                              "payments": [{"method_id": 1, "amount": "13000"}], "idempotency_key": "k" * 20})
    assert r.status_code == 201, r.text
    r = owner_client.post(f"/api/stock-adjustments/{adj['id']}/approve", json={})
    assert r.status_code == 409 and "3 available, 4 needed" in r.json()["detail"]
    assert qty(owner_client, charger) == 3
    pending = owner_client.get("/api/stock-adjustments", params={"status": "pending"}).json()
    assert pending["total"] == 1  # still waiting; the owner can reject it


def test_adjustment_validation(owner_client, staff_client, stock):
    charger, a54, unit = stock["charger"]["id"], stock["phone"]["id"], stock["unit"]
    cases = [
        ([{"product_id": charger, "quantity_change": 2, "kind": "damage"}], "negative number"),
        ([{"product_id": charger}], "how many"),
        ([{"product_id": charger, "unit_id": unit["id"], "unit_new_status": "damaged"}], "counted by quantity"),
        ([{"product_id": a54, "quantity_change": -1}], "pick"),
        ([{"product_id": a54, "unit_id": unit["id"], "unit_new_status": "in_stock"}], "already in stock"),
        ([{"product_id": 999, "quantity_change": 1}], "does not exist"),
    ]
    for lines, needle in cases:
        r = request_(staff_client, lines)
        assert r.status_code == 422 and needle in r.json()["detail"], (lines, r.json())
    assert request_(staff_client, [{"product_id": charger, "quantity_change": 1}], reason="x").status_code == 422

    r = staff_client.post("/api/sales", json={"lines": [{"product_id": a54, "unit_id": unit["id"]}],
                                              "payments": [{"method_id": 1, "amount": "420000"}], "idempotency_key": "s" * 20})
    assert r.status_code == 201, r.text
    r = request_(staff_client, [{"product_id": a54, "unit_id": unit["id"], "unit_new_status": "missing"}])
    assert r.status_code == 409 and "sold" in r.json()["detail"]


def test_staff_see_their_own_requests_owner_sees_all(owner_client, staff_client, stock):
    make_user("musa", "staff")
    musa = logged_in("musa")
    request_(staff_client, [{"product_id": stock["charger"]["id"], "quantity_change": 1}])
    request_(musa, [{"product_id": stock["charger"]["id"], "quantity_change": 2}])
    assert staff_client.get("/api/stock-adjustments").json()["total"] == 1
    assert musa.get("/api/stock-adjustments").json()["total"] == 1
    assert owner_client.get("/api/stock-adjustments").json()["total"] == 2
