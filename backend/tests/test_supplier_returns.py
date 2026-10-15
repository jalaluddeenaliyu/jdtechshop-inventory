"""Supplier returns, purchase reversals and supplier payment reversals."""
import uuid

import pytest

from app.core.clock import business_today
from tests.test_products import make_product, opening
from tests.test_units import imei, phone

TODAY = business_today().isoformat()


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def bought(owner_client):
    """Charger: 10 on the shelf at ₦4,000, then 10 bought at ₦4,400 (+₦400 transport on that line).
    Phone: two units bought at ₦378,000."""
    supplier = owner_client.post("/api/suppliers", json={"name": "Slot"}).json()
    charger = make_product(owner_client, name="Charger")
    opening(owner_client, charger["id"], 10, "4000")
    a54 = phone(owner_client, name="Galaxy A54")
    p = owner_client.post("/api/purchases", json={"supplier_id": supplier["id"], "purchase_date": TODAY,
                                                  "additional_costs": "8000", "lines": [
        {"product_id": charger["id"], "quantity": 10, "unit_cost": "4400"},
        {"product_id": a54["id"], "quantity": 2, "unit_cost": "378000",
         "units": [{"imei1": imei(1)}, {"imei1": imei(2)}]}]}).json()
    p = owner_client.post(f"/api/purchases/{p['id']}/post").json()
    units = owner_client.get("/api/units", params={"product_id": a54["id"]}).json()["items"]
    return {"supplier": supplier, "charger": charger, "phone": a54, "purchase": p, "units": units}


def send_back(client, purchase_id, lines, **extra):
    return client.post(f"/api/purchases/{purchase_id}/returns", json={
        "lines": lines, "reason": extra.pop("reason", "Faulty batch"),
        "idempotency_key": extra.pop("idempotency_key", key()), **extra})


def get(client, path):
    return client.get(path).json()


def test_return_quantity_items_to_supplier(owner_client, bought):
    p, charger = bought["purchase"], bought["charger"]
    before = get(owner_client, f"/api/products/{charger['id']}")
    assert before["qty_on_hand"] == 20 and before["avg_cost"] == "4222.0000"
    r = send_back(owner_client, p["id"], [{"purchase_line_id": p["lines"][0]["id"], "quantity": 3}])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["returned_credit"] == "13200.00"  # 3 × invoice price 4,400
    assert body["balance_due"] == "794800.00"     # 808,000 − 13,200
    assert body["returns"][0]["items"] == ["Charger × 3"]
    after = get(owner_client, f"/api/products/{charger['id']}")
    # Stock left at its landed cost (4,444), so the average is unwound exactly:
    # (20 × 4,222 − 3 × 4,444) ÷ 17 = 4,182.8235…
    assert after["qty_on_hand"] == 17 and after["avg_cost"] == "4182.8235"
    assert get(owner_client, f"/api/suppliers/{bought['supplier']['id']}")["balance"] == "794800.00"

    r = send_back(owner_client, p["id"], [{"purchase_line_id": p["lines"][0]["id"], "quantity": 8}])
    assert r.status_code == 409 and "Only 7 Charger" in r.json()["detail"]


def test_return_phone_units_to_supplier(owner_client, staff_client, bought):
    p, (u1, u2) = bought["purchase"], bought["units"]
    line = p["lines"][1]["id"]
    r = send_back(owner_client, p["id"], [{"purchase_line_id": line, "quantity": 1}])
    assert r.status_code == 422 and "Pick which" in r.json()["detail"]
    r = send_back(owner_client, p["id"], [{"purchase_line_id": line, "quantity": 1, "unit_ids": [u1["id"]]}])
    assert r.status_code == 201
    assert get(owner_client, f"/api/units/{u1['id']}")["status"] == "returned_to_supplier"
    assert get(owner_client, f"/api/products/{bought['phone']['id']}")["qty_on_hand"] == 1
    assert r.json()["returned_credit"] == "378000.00"

    # A sold unit cannot go back
    staff_client.post("/api/sales", json={"lines": [{"product_id": bought["phone"]["id"], "unit_id": u2["id"]}],
                                          "payments": [{"method_id": 1, "amount": "420000"}], "idempotency_key": key()})
    other = phone(owner_client, name="Unrelated")
    stray = owner_client.post(f"/api/products/{other['id']}/units/opening",
                              json={"units": [{"imei1": imei(9), "unit_cost": "1"}]}).json()["units"][0]
    r = send_back(owner_client, p["id"], [{"purchase_line_id": line, "quantity": 1, "unit_ids": [stray["id"]]}])
    assert r.status_code == 422 and "did not come in" in r.json()["detail"]


def test_credit_beyond_what_is_owed_means_supplier_owes_the_shop(owner_client, bought):
    p, s = bought["purchase"], bought["supplier"]
    owner_client.post("/api/supplier-payments", json={"supplier_id": s["id"], "amount": "808000", "method_id": 1,
                                                      "idempotency_key": key()})
    send_back(owner_client, p["id"], [{"purchase_line_id": p["lines"][0]["id"], "quantity": 2}])
    assert get(owner_client, f"/api/suppliers/{s['id']}")["balance"] == "-8800.00"
    r = owner_client.post("/api/supplier-payments", json={"supplier_id": s["id"], "amount": "1", "method_id": 1,
                                                          "idempotency_key": key()})
    assert r.status_code == 409


def test_reversing_a_purchase_unwinds_everything(owner_client, bought):
    p, charger = bought["purchase"], bought["charger"]
    r = owner_client.post(f"/api/purchases/{p['id']}/reverse", json={"reason": "Entered twice"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reversed" and r.json()["reversal_reason"] == "Entered twice"
    after = get(owner_client, f"/api/products/{charger['id']}")
    assert after["qty_on_hand"] == 10 and after["avg_cost"] == "4000.0000"  # back to before the purchase
    assert {u["status"] for u in get(owner_client, f"/api/units?product_id={bought['phone']['id']}")["items"]} == {"voided"}
    assert get(owner_client, f"/api/products/{bought['phone']['id']}")["qty_on_hand"] == 0
    assert get(owner_client, f"/api/suppliers/{bought['supplier']['id']}")["balance"] == "0.00"
    # Again: no change. Returns: not possible any more.
    assert owner_client.post(f"/api/purchases/{p['id']}/reverse", json={"reason": "again"}).status_code == 200
    r = send_back(owner_client, p["id"], [{"purchase_line_id": p["lines"][0]["id"], "quantity": 1}])
    assert r.status_code == 409


def test_purchase_reversal_is_refused_when_things_have_happened(owner_client, staff_client, bought):
    p, s = bought["purchase"], bought["supplier"]
    pay = owner_client.post("/api/supplier-payments", json={
        "supplier_id": s["id"], "purchase_id": p["id"], "amount": "1000", "method_id": 1,
        "idempotency_key": key()}).json()
    r = owner_client.post(f"/api/purchases/{p['id']}/reverse", json={"reason": "Mistake"})
    assert r.status_code == 409 and "Reverse those payments first" in r.json()["detail"]

    # Reverse the payment, then the purchase is blocked by a sold phone instead
    r = owner_client.post(f"/api/supplier-payments/{pay['id']}/reverse", json={"reason": "Paid wrong supplier"})
    assert r.status_code == 200 and r.json()["reversed"] is True
    assert get(owner_client, f"/api/suppliers/{s['id']}")["balance"] == "808000.00"
    r = staff_client.post("/api/sales", json={"lines": [{"product_id": bought["phone"]["id"], "unit_id": bought["units"][0]["id"]}],
                                              "payments": [{"method_id": 1, "amount": "420000"}], "idempotency_key": key()})
    assert r.status_code == 201, r.text
    r = owner_client.post(f"/api/purchases/{p['id']}/reverse", json={"reason": "Mistake"})
    assert r.status_code == 409 and "sold" in r.json()["detail"]


def test_purchase_reversal_needs_the_stock_still_here(owner_client, staff_client, bought):
    p = bought["purchase"]
    r = staff_client.post("/api/sales", json={"lines": [{"product_id": bought["charger"]["id"], "quantity": 11}],
                                              "payments": [{"method_id": 1, "amount": "71500"}], "idempotency_key": key()})
    assert r.status_code == 201, r.text
    r = owner_client.post(f"/api/purchases/{p['id']}/reverse", json={"reason": "Mistake"})
    assert r.status_code == 409 and "only 9 of the 10 Charger" in r.json()["detail"]
    draft = owner_client.post("/api/purchases", json={"supplier_id": bought["supplier"]["id"],
                                                      "purchase_date": TODAY}).json()
    r = owner_client.post(f"/api/purchases/{draft['id']}/reverse", json={"reason": "Mistake"})
    assert r.status_code == 409 and "discard" in r.json()["detail"]


def test_supplier_returns_and_reversals_are_owner_only(staff_client, owner_client, bought):
    p = bought["purchase"]
    assert send_back(staff_client, p["id"], [{"purchase_line_id": p["lines"][0]["id"]}]).status_code == 403
    assert staff_client.post(f"/api/purchases/{p['id']}/reverse", json={"reason": "abc"}).status_code == 403
    assert owner_client.post(f"/api/purchases/{p['id']}/reverse", json={"reason": "a"}).status_code == 422
