"""Customer returns, sale reversals and customer payment reversals."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database import engine
from tests.test_products import make_product, opening
from tests.test_units import imei, phone


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def shop(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    a54 = phone(owner_client, name="Galaxy A54", selling_price="420000")
    units = owner_client.post(f"/api/products/{a54['id']}/units/opening", json={"units": [
        {"imei1": imei(1), "unit_cost": "380000"}, {"imei1": imei(2), "unit_cost": "380000"}]}).json()["units"]
    aisha = staff_client.post("/api/customers", json={"name": "Aisha Bello"}).json()
    return {"charger": charger, "phone": a54, "units": units, "aisha": aisha}


def sell(client, lines, paid=None, **extra) -> dict:
    r = client.post("/api/sales", json={"lines": lines, "idempotency_key": key(),
                                        "payments": [{"method_id": 1, "amount": paid}] if paid else [], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def give_back(client, sale_id, items, refund_method_id=1, **extra):
    return client.post(f"/api/sales/{sale_id}/returns", json={
        "lines": items, "reason": extra.pop("reason", "Customer changed mind"),
        "refund_method_id": refund_method_id, "idempotency_key": extra.pop("idempotency_key", key()), **extra})


def qty(client, product_id) -> int:
    return client.get(f"/api/products/{product_id}").json()["qty_on_hand"]


def test_return_from_a_cash_sale_refunds_and_restocks(owner_client, staff_client, shop):
    sale = sell(staff_client, [{"product_id": shop["charger"]["id"], "quantity": 2},
                               {"product_id": shop["phone"]["id"], "unit_id": shop["units"][0]["id"]}], "433000")
    charger_line = sale["lines"][0]["id"]

    # Money is due back, so the refund method is required
    r = give_back(owner_client, sale["id"], [{"sale_line_id": charger_line}], refund_method_id=None)
    assert r.status_code == 422 and "₦6,500.00 must be given back" in r.json()["detail"]

    r = give_back(owner_client, sale["id"], [{"sale_line_id": charger_line}])
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["returned_total"] == "6500.00" and s["amount_paid"] == "426500.00" and s["balance_due"] == "0.00"
    assert s["returns"][0]["refund_amount"] == "6500.00" and s["returns"][0]["value_total"] == "6500.00"
    # Profit = (433,000 − 6,500) − (2 × 4,000 + 380,000 − 4,000)
    assert s["profit"] == "42500.00"
    assert qty(owner_client, shop["charger"]["id"]) == 9
    moves = owner_client.get("/api/stock-movements", params={"movement_type": "customer_return"}).json()["items"]
    assert moves[0]["quantity_change"] == 1 and moves[0]["unit_cost"] == "4000.0000"
    log = owner_client.get("/api/audit-logs", params={"action": "sale.returned"}).json()["items"][0]
    assert log["details"]["refund"] == "6500.00"


def test_damaged_return_goes_in_and_straight_out(owner_client, staff_client, shop):
    unit = shop["units"][0]
    sale = sell(staff_client, [{"product_id": shop["phone"]["id"], "unit_id": unit["id"]}], "420000")
    r = give_back(owner_client, sale["id"], [{"sale_line_id": sale["lines"][0]["id"], "restock": False}],
                  reason="Screen cracked, refunded")
    assert r.status_code == 201
    assert owner_client.get(f"/api/units/{unit['id']}").json()["status"] == "damaged"
    assert qty(owner_client, shop["phone"]["id"]) == 1  # the other unit only
    types = [m["movement_type"] for m in owner_client.get(
        "/api/stock-movements", params={"product_id": shop["phone"]["id"]}).json()["items"]]
    assert types[:2] == ["damage", "customer_return"]


def test_return_value_follows_the_discount_and_adds_up_exactly(owner_client, shop):
    sale = sell(owner_client, [{"product_id": shop["charger"]["id"], "quantity": 3, "discount": "100"}], "19400")
    line = sale["lines"][0]["id"]
    assert give_back(owner_client, sale["id"], [{"sale_line_id": line}]).json()["returned_total"] == "6466.67"
    r = give_back(owner_client, sale["id"], [{"sale_line_id": line, "quantity": 2}])
    assert r.json()["returned_total"] == "19400.00"  # the last items take the remainder
    r = give_back(owner_client, sale["id"], [{"sale_line_id": line}])
    assert r.status_code == 409 and "Only 0 Charger" in r.json()["detail"]
    assert give_back(owner_client, sale["id"], [{"sale_line_id": 99999}]).status_code == 422


def test_return_on_credit_reduces_the_debt_before_refunding(owner_client, staff_client, shop):
    aisha = shop["aisha"]["id"]
    sale = sell(staff_client, [{"product_id": shop["charger"]["id"], "quantity": 2}], "3000", customer_id=aisha)
    line = sale["lines"][0]["id"]
    # First charger back: 6,500 off the 10,000 owed, no refund needed
    r = give_back(owner_client, sale["id"], [{"sale_line_id": line}], refund_method_id=None)
    assert r.status_code == 201 and r.json()["returns"][0]["refund_amount"] == "0.00"
    assert owner_client.get(f"/api/customers/{aisha}").json()["owed"] == "3500.00"
    # Second charger back: the 3,000 she paid comes back to her
    r = give_back(owner_client, sale["id"], [{"sale_line_id": line}])
    assert r.json()["returns"][1]["refund_amount"] == "3000.00"
    assert r.json()["balance_due"] == "0.00" and r.json()["amount_paid"] == "0.00"
    assert owner_client.get(f"/api/customers/{aisha}").json()["owed"] == "0.00"


def test_same_return_form_twice_returns_once(owner_client, shop):
    sale = sell(owner_client, [{"product_id": shop["charger"]["id"], "quantity": 2}], "13000")
    k = key()
    first = give_back(owner_client, sale["id"], [{"sale_line_id": sale["lines"][0]["id"]}], idempotency_key=k)
    again = give_back(owner_client, sale["id"], [{"sale_line_id": sale["lines"][0]["id"]}], idempotency_key=k)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["returned_total"] == "6500.00"
    assert qty(owner_client, shop["charger"]["id"]) == 9


def test_reversing_a_sale(owner_client, staff_client, shop):
    aisha = shop["aisha"]["id"]
    unit = shop["units"][1]
    sale = sell(staff_client, [{"product_id": shop["charger"]["id"], "quantity": 2},
                               {"product_id": shop["phone"]["id"], "unit_id": unit["id"]}], "100000", customer_id=aisha)
    staff_client.post("/api/customer-payments", json={"customer_id": aisha, "amount": "50000", "method_id": 1,
                                                      "idempotency_key": key()})
    r = owner_client.post(f"/api/sales/{sale['id']}/reverse", json={"reason": "Keyed on the wrong customer"})
    assert r.status_code == 422 and "₦150,000.00 must be given back" in r.json()["detail"]
    r = owner_client.post(f"/api/sales/{sale['id']}/reverse",
                          json={"reason": "Keyed on the wrong customer", "refund_method_id": 1})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"] == "reversed" and s["reversal_refund"] == "150000.00" and s["balance_due"] == "0.00"
    assert s["reversed_by_name"] == "Jalal Owner" and s["profit"] == "0.00"
    assert qty(owner_client, shop["charger"]["id"]) == 10
    assert owner_client.get(f"/api/units/{unit['id']}").json()["status"] == "in_stock"
    assert owner_client.get(f"/api/customers/{aisha}").json()["owed"] == "0.00"
    assert owner_client.get("/api/sales", params={"unpaid_only": True}).json()["total"] == 0

    # Reversing again changes nothing; returns are no longer possible
    assert owner_client.post(f"/api/sales/{sale['id']}/reverse", json={"reason": "again"}).json()["status"] == "reversed"
    r = give_back(owner_client, sale["id"], [{"sale_line_id": s["lines"][0]["id"]}])
    assert r.status_code == 409 and "reversed" in r.json()["detail"]
    # The database allows the single reversal only
    with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE sales SET reversal_reason = 'edited'"))
    log = owner_client.get("/api/audit-logs", params={"action": "sale.reversed"}).json()["items"][0]
    assert log["details"]["reason"] == "Keyed on the wrong customer"


def test_cannot_reverse_a_sale_that_has_returns(owner_client, shop):
    sale = sell(owner_client, [{"product_id": shop["charger"]["id"], "quantity": 2}], "13000")
    give_back(owner_client, sale["id"], [{"sale_line_id": sale["lines"][0]["id"]}])
    r = owner_client.post(f"/api/sales/{sale['id']}/reverse", json={"reason": "Mistake", "refund_method_id": 1})
    assert r.status_code == 409 and "Return the remaining items" in r.json()["detail"]


def test_returns_and_reversals_are_owner_only(staff_client, owner, shop):
    sale = sell(staff_client, [{"product_id": shop["charger"]["id"]}], "6500")
    assert give_back(staff_client, sale["id"], [{"sale_line_id": sale["lines"][0]["id"]}]).status_code == 403
    assert staff_client.post(f"/api/sales/{sale['id']}/reverse", json={"reason": "x y z"}).status_code == 403
    r = give_back(staff_client, sale["id"], [{"sale_line_id": sale["lines"][0]["id"]}], reason="no")
    assert r.status_code in (403, 422)


def test_reversing_a_customer_payment(owner_client, staff_client, shop):
    aisha = shop["aisha"]["id"]
    sell(staff_client, [{"product_id": shop["charger"]["id"], "quantity": 2}], customer_id=aisha)
    p = staff_client.post("/api/customer-payments", json={"customer_id": aisha, "amount": "5000", "method_id": 1,
                                                          "idempotency_key": key()}).json()
    assert owner_client.get(f"/api/customers/{aisha}").json()["owed"] == "8000.00"
    assert staff_client.post(f"/api/customer-payments/{p['id']}/reverse", json={"reason": "Wrong customer"}).status_code == 403
    r = owner_client.post(f"/api/customer-payments/{p['id']}/reverse", json={"reason": "Wrong customer"})
    assert r.status_code == 200 and r.json()["reversed"] is True and r.json()["reversal_reason"] == "Wrong customer"
    assert owner_client.get(f"/api/customers/{aisha}").json()["owed"] == "13000.00"
    assert owner_client.post(f"/api/customer-payments/{p['id']}/reverse", json={"reason": "again"}).status_code == 200
    assert owner_client.post("/api/customer-payments/999/reverse", json={"reason": "nope"}).status_code == 404


def test_payment_on_a_returned_sale_cannot_be_reversed(owner_client, staff_client, shop):
    aisha = shop["aisha"]["id"]
    sale = sell(staff_client, [{"product_id": shop["charger"]["id"], "quantity": 2}], "5000", customer_id=aisha)
    give_back(owner_client, sale["id"], [{"sale_line_id": sale["lines"][0]["id"]}])
    till_payment = staff_client.get("/api/customer-payments", params={"customer_id": aisha}).json()["items"][0]
    r = owner_client.post(f"/api/customer-payments/{till_payment['id']}/reverse", json={"reason": "Mistake"})
    assert r.status_code == 409 and "returned or reversed" in r.json()["detail"]
