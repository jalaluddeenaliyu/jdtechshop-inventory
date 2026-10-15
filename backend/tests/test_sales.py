"""Point of sale: locking, discounts, credit rules, frozen cost, idempotency and receipts."""
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import business_today
from app.database import engine
from tests.conftest import logged_in
from tests.test_products import make_product, opening
from tests.test_units import imei, phone

TODAY = business_today()
RECEIPT_PREFIX = f"JDT-{TODAY:%y%m%d}-"


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def stock(owner_client):
    """A charger (10 at ₦4,000, sells ₦6,500) and a phone with two units (sells ₦420,000)."""
    charger = make_product(owner_client, name="Oraimo 20W charger", selling_price="6500",
                           barcode="6941057400123")
    opening(owner_client, charger["id"], 10, "4000")
    a54 = phone(owner_client, name="Galaxy A54", selling_price="420000")
    units = owner_client.post(f"/api/products/{a54['id']}/units/opening", json={"units": [
        {"imei1": imei(1), "unit_cost": "380000"},
        {"imei1": imei(2), "serial_number": "R58X2", "unit_cost": "375000", "condition": "used"},
    ]}).json()["units"]
    return {"charger": charger, "phone": a54, "units": units}


def sell(client, lines, payments=None, **extra):
    return client.post("/api/sales", json={
        "lines": lines, "payments": payments or [], "idempotency_key": extra.pop("idempotency_key", key()),
        **extra})


def cash(amount: str) -> list[dict]:
    return [{"method_id": 1, "amount": amount}]


def qty(client, product_id) -> int:
    return client.get(f"/api/products/{product_id}").json()["qty_on_hand"]


def test_cash_sale_to_walk_in(owner_client, staff_client, stock):
    unit = stock["units"][0]
    r = sell(staff_client, [
        {"product_id": stock["charger"]["id"], "quantity": 2},
        {"product_id": stock["phone"]["id"], "unit_id": unit["id"]},
    ], cash("433000"))
    assert r.status_code == 201, r.text
    sale = r.json()
    assert sale["receipt_no"] == RECEIPT_PREFIX + "0001"
    assert sale["is_walk_in"] is True and sale["customer_name"] == "Walk-in customer"
    assert sale["sold_by_name"] == "Amina Cashier" and sale["business_date"] == TODAY.isoformat()
    assert sale["subtotal"] == "433000.00" and sale["total"] == "433000.00"
    assert sale["amount_paid"] == "433000.00" and sale["balance_due"] == "0.00"
    assert sale["item_count"] == 3
    # Staff never see cost or profit
    assert sale["profit"] is None and all(line["unit_cost_snapshot"] is None for line in sale["lines"])
    assert sale["lines"][1]["imei1"] == imei(1) and sale["payments"][0]["method_name"] == "Cash"

    # The owner sees profit from frozen cost: 433,000 − (2 × 4,000 + 380,000) = 45,000
    owner_view = owner_client.get(f"/api/sales/{sale['id']}").json()
    assert owner_view["profit"] == "45000.00"
    assert owner_view["lines"][0]["unit_cost_snapshot"] == "4000.0000"

    assert qty(owner_client, stock["charger"]["id"]) == 8
    assert qty(owner_client, stock["phone"]["id"]) == 1
    sold = owner_client.get(f"/api/units/{unit['id']}").json()
    assert sold["status"] == "sold"
    moves = owner_client.get("/api/stock-movements", params={"movement_type": "sale"}).json()["items"]
    assert sorted(m["quantity_change"] for m in moves) == [-2, -1]
    assert {(m["reference_type"], m["reference_id"]) for m in moves} == {("sale", sale["id"])}

    # Receipt numbers count up through the day
    r = sell(staff_client, [{"product_id": stock["charger"]["id"]}], cash("6500"))
    assert r.json()["receipt_no"] == RECEIPT_PREFIX + "0002"
    log = owner_client.get("/api/audit-logs", params={"action": "sale.completed"}).json()
    assert log["total"] == 2


def test_price_always_comes_from_the_product(staff_client, stock):
    r = sell(staff_client, [{"product_id": stock["charger"]["id"], "unit_price": "1"}], cash("1"))
    assert r.status_code == 422
    r = sell(staff_client, [{"product_id": stock["charger"]["id"]}], cash("6500"))
    assert r.json()["lines"][0]["unit_price"] == "6500.00"


def test_discount_limits(owner_client, staff_client, stock):
    charger = stock["charger"]["id"]
    # Staff limit is 5% of the line: 2 × 6,500 = 13,000 → up to 650
    r = sell(staff_client, [{"product_id": charger, "quantity": 2, "discount": "650"}], cash("12350"))
    assert r.status_code == 201 and r.json()["discount_total"] == "650.00"
    assert r.json()["total"] == "12350.00"

    r = sell(staff_client, [{"product_id": charger, "quantity": 2, "discount": "650.01"}], cash("12349.99"))
    assert r.status_code == 403 and "Ask the owner" in r.json()["detail"] and "5% = ₦650.00" in r.json()["detail"]
    assert qty(owner_client, charger) == 8  # nothing happened

    # The owner may give more, but never more than the price
    r = sell(owner_client, [{"product_id": charger, "discount": "3000"}], cash("3500"))
    assert r.status_code == 201
    r = sell(owner_client, [{"product_id": charger, "discount": "6500.01"}])
    assert r.status_code == 422 and "more than the price" in r.json()["detail"]
    # A refused sale does not use up a receipt number
    assert r.status_code == 422
    nxt = sell(owner_client, [{"product_id": charger}], cash("6500")).json()["receipt_no"]
    assert nxt == RECEIPT_PREFIX + "0003"

    # The owner can change the staff limit in Settings
    s = owner_client.get("/api/settings").json()
    owner_client.put("/api/settings", json={"business_name": s["business_name"], "default_reorder_level": 3,
                                            "staff_max_discount_percent": "10",
                                            "allow_staff_credit_sales": True})
    r = sell(staff_client, [{"product_id": charger, "discount": "650"}], cash("5850"))
    assert r.status_code == 201


def test_credit_sales(owner_client, staff_client, stock):
    charger = stock["charger"]["id"]
    aisha = staff_client.post("/api/customers", json={"name": "Aisha Bello", "phone": "08031234567"}).json()

    r = sell(staff_client, [{"product_id": charger}], cash("5000"))
    assert r.status_code == 422 and "Walk-in customers must pay in full" in r.json()["detail"]

    due = (TODAY + timedelta(days=14)).isoformat()
    r = sell(staff_client, [{"product_id": charger, "quantity": 2}], cash("5000"),
             customer_id=aisha["id"], due_date=due)
    assert r.status_code == 201, r.text
    assert r.json()["balance_due"] == "8000.00" and r.json()["due_date"] == due
    # Nothing paid at all is also a credit sale
    r = sell(staff_client, [{"product_id": charger}], customer_id=aisha["id"])
    assert r.status_code == 201 and r.json()["amount_paid"] == "0.00"

    r = sell(staff_client, [{"product_id": charger}], cash("1"), customer_id=aisha["id"],
             due_date=(TODAY - timedelta(days=1)).isoformat())
    assert r.status_code == 422 and "past" in r.json()["detail"]
    r = sell(staff_client, [{"product_id": charger}], cash("6500.01"))
    assert r.status_code == 422 and "more than the total" in r.json()["detail"]

    unpaid = owner_client.get("/api/sales", params={"unpaid_only": True}).json()
    assert unpaid["total"] == 2

    # The owner can stop staff selling on credit
    s = owner_client.get("/api/settings").json()
    owner_client.put("/api/settings", json={"business_name": s["business_name"], "default_reorder_level": 3,
                                            "staff_max_discount_percent": "5",
                                            "allow_staff_credit_sales": False})
    r = sell(staff_client, [{"product_id": charger}], cash("1"), customer_id=aisha["id"])
    assert r.status_code == 403 and "credit" in r.json()["detail"]
    assert sell(owner_client, [{"product_id": charger}], cash("1"), customer_id=aisha["id"]).status_code == 201

    owner_client.patch(f"/api/customers/{aisha['id']}", json={"is_active": False})
    r = sell(owner_client, [{"product_id": charger}], cash("6500"), customer_id=aisha["id"])
    assert r.status_code == 422


def test_serial_imei_rules(owner_client, staff_client, stock):
    a54, (u1, u2) = stock["phone"]["id"], stock["units"]
    r = sell(staff_client, [{"product_id": a54}], cash("420000"))
    assert r.status_code == 422 and "choose which" in r.json()["detail"]
    r = sell(staff_client, [{"product_id": a54, "unit_id": u1["id"], "quantity": 2}], cash("1"))
    assert r.status_code == 422
    r = sell(staff_client, [{"product_id": a54, "unit_id": u1["id"]},
                            {"product_id": a54, "unit_id": u1["id"]}], cash("1"))
    assert r.status_code == 422 and "twice" in r.json()["detail"]
    r = sell(staff_client, [{"product_id": stock["charger"]["id"], "unit_id": u1["id"]}], cash("1"))
    assert r.status_code == 422
    other = phone(owner_client, name="Other phone")
    r = sell(staff_client, [{"product_id": other["id"], "unit_id": u1["id"]}], cash("1"))
    assert r.status_code == 422 and "not a Other phone" in r.json()["detail"]

    assert sell(staff_client, [{"product_id": a54, "unit_id": u1["id"]}], cash("420000")).status_code == 201
    r = sell(staff_client, [{"product_id": a54, "unit_id": u1["id"]}], cash("420000"))
    assert r.status_code == 409 and "already sold" in r.json()["detail"] and imei(1) in r.json()["detail"]
    assert qty(owner_client, a54) == 1


def test_cannot_oversell_and_nothing_partial_is_saved(owner_client, staff_client, stock):
    r = sell(staff_client, [{"product_id": stock["phone"]["id"], "unit_id": stock["units"][0]["id"]},
                            {"product_id": stock["charger"]["id"], "quantity": 11}], cash("1"))
    assert r.status_code == 409 and "10 available, 11 needed" in r.json()["detail"]
    # The phone line before it was not saved either
    assert owner_client.get(f"/api/units/{stock['units'][0]['id']}").json()["status"] == "in_stock"
    assert qty(owner_client, stock["charger"]["id"]) == 10
    assert owner_client.get("/api/sales").json()["total"] == 0


def test_turned_off_things_cannot_be_sold_or_used(owner_client, staff_client, stock):
    owner_client.patch("/api/payment-methods/2", json={"is_active": False})
    r = sell(staff_client, [{"product_id": stock["charger"]["id"]}], [{"method_id": 2, "amount": "6500"}])
    assert r.status_code == 422 and "payment method" in r.json()["detail"]
    owner_client.patch(f"/api/products/{stock['charger']['id']}", json={"is_active": False})
    r = sell(staff_client, [{"product_id": stock["charger"]["id"]}], cash("6500"))
    assert r.status_code == 422 and "turned off" in r.json()["detail"]
    assert sell(staff_client, [{"product_id": 999}], cash("1")).status_code == 422
    assert sell(staff_client, [], cash("1")).status_code == 422


def test_split_payment(staff_client, stock):
    r = sell(staff_client, [{"product_id": stock["phone"]["id"], "unit_id": stock["units"][1]["id"]}],
             [{"method_id": 1, "amount": "20000"}, {"method_id": 2, "amount": "400000", "reference": "TRF-77"}])
    assert r.status_code == 201
    assert [(p["method_name"], p["amount"]) for p in r.json()["payments"]] == [
        ("Cash", "20000.00"), ("Bank transfer", "400000.00")]
    assert r.json()["lines"][0]["condition"] == "used"


def test_same_cart_twice_records_one_sale(owner_client, staff_client, stock):
    k = key()
    first = sell(staff_client, [{"product_id": stock["charger"]["id"], "quantity": 3}], cash("19500"), idempotency_key=k)
    again = sell(staff_client, [{"product_id": stock["charger"]["id"], "quantity": 3}], cash("19500"), idempotency_key=k)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert qty(owner_client, stock["charger"]["id"]) == 7


def _race(n_clients: int, body_for):
    clients = [logged_in("cashier") for _ in range(n_clients)]
    barrier = threading.Barrier(n_clients)
    results: list = []

    def go(i, c):
        barrier.wait()
        results.append(c.post("/api/sales", json=body_for(i)))

    threads = [threading.Thread(target=go, args=(i, c)) for i, c in enumerate(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_two_cashiers_cannot_sell_the_last_charger(owner_client, staff, owner):
    charger = make_product(owner_client, name="Last charger", selling_price="6500")
    opening(owner_client, charger["id"], 1, "4000")
    results = _race(2, lambda i: {"lines": [{"product_id": charger["id"]}], "payments": cash("6500"),
                                  "idempotency_key": key()})
    assert sorted(r.status_code for r in results) == [201, 409]
    assert qty(owner_client, charger["id"]) == 0
    assert owner_client.get("/api/sales").json()["total"] == 1


def test_two_cashiers_cannot_sell_the_same_phone(owner_client, staff, stock):
    unit = stock["units"][0]
    results = _race(2, lambda i: {"lines": [{"product_id": stock["phone"]["id"], "unit_id": unit["id"]}],
                                  "payments": cash("420000"), "idempotency_key": key()})
    assert sorted(r.status_code for r in results) == [201, 409]
    assert qty(owner_client, stock["phone"]["id"]) == 1


def test_double_click_at_the_same_moment_makes_one_sale(owner_client, staff, stock):
    k = key()
    results = _race(2, lambda i: {"lines": [{"product_id": stock["charger"]["id"]}], "payments": cash("6500"),
                                  "idempotency_key": k})
    assert sorted(r.status_code for r in results) == [200, 201]
    assert results[0].json()["id"] == results[1].json()["id"]
    assert qty(owner_client, stock["charger"]["id"]) == 9


def test_profit_is_frozen_when_costs_change_later(owner_client, staff_client, stock):
    sale = sell(staff_client, [{"product_id": stock["charger"]["id"]}], cash("6500")).json()
    before = owner_client.get(f"/api/sales/{sale['id']}").json()["profit"]
    assert before == "2500.00"
    # A new purchase at a much higher cost moves the average…
    s = owner_client.post("/api/suppliers", json={"name": "Supplier"}).json()
    p = owner_client.post("/api/purchases", json={"supplier_id": s["id"], "purchase_date": TODAY.isoformat(),
                                                  "lines": [{"product_id": stock["charger"]["id"],
                                                             "quantity": 9, "unit_cost": "6000"}]}).json()
    owner_client.post(f"/api/purchases/{p['id']}/post")
    assert owner_client.get(f"/api/products/{stock['charger']['id']}").json()["avg_cost"] == "5000.0000"
    # …but the past sale's profit does not change
    assert owner_client.get(f"/api/sales/{sale['id']}").json()["profit"] == before


@pytest.mark.parametrize("sql", [
    "UPDATE sales SET total = 1, subtotal = 1",
    "DELETE FROM sales",
    "UPDATE sale_lines SET unit_price = 1",
    "DELETE FROM customer_payments",
])
def test_sales_are_permanent(staff_client, stock, sql):
    sell(staff_client, [{"product_id": stock["charger"]["id"]}], cash("6500"))
    with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
        with engine.begin() as conn:
            conn.execute(text(sql))


def test_pos_search(staff_client, stock, owner_client):
    def kinds(q):
        return [(r["kind"], r["product"]["name"], (r["unit"] or {}).get("imei1"))
                for r in staff_client.get("/api/pos/search", params={"q": q}).json()]

    # A scanned IMEI finds exactly that phone
    assert kinds(imei(2)) == [("unit", "Galaxy A54", imei(2))]
    assert kinds("r58x2") == [("unit", "Galaxy A54", imei(2))]  # serial, any case
    # Part of an IMEI lists matching phones first, then products
    assert kinds(imei(1)[-6:])[0] == ("unit", "Galaxy A54", imei(1))
    # Scanned barcode, name, SKU
    assert kinds("6941057400123") == [("product", "Oraimo 20W charger", None)]
    assert kinds("oraimo") == [("product", "Oraimo 20W charger", None)]
    assert kinds(stock["charger"]["sku"])[0][1] == "Oraimo 20W charger"
    # Sold phones are not offered
    sell(staff_client, [{"product_id": stock["phone"]["id"], "unit_id": stock["units"][1]["id"]}], cash("420000"))
    assert ("unit", "Galaxy A54", imei(2)) not in kinds(imei(2))
    assert staff_client.get("/api/pos/search", params={"q": ""}).status_code == 422


def test_sales_history(owner_client, staff_client, stock):
    aisha = staff_client.post("/api/customers", json={"name": "Aisha Bello"}).json()
    s1 = sell(staff_client, [{"product_id": stock["charger"]["id"]}], cash("6500")).json()
    s2 = sell(owner_client, [{"product_id": stock["charger"]["id"]}], customer_id=aisha["id"]).json()

    def receipts(client=owner_client, **params):
        return [s["receipt_no"] for s in client.get("/api/sales", params=params).json()["items"]]

    assert receipts() == [s2["receipt_no"], s1["receipt_no"]]  # newest first
    assert receipts(q="aisha") == [s2["receipt_no"]]
    assert receipts(q=s1["receipt_no"][-4:]) == [s1["receipt_no"]]
    assert receipts(unpaid_only=True) == [s2["receipt_no"]]
    assert receipts(date_from=TODAY.isoformat(), date_to=TODAY.isoformat()) == [s2["receipt_no"], s1["receipt_no"]]
    assert receipts(date_to=(TODAY - timedelta(days=1)).isoformat()) == []
    assert receipts(customer_id=aisha["id"]) == [s2["receipt_no"]]
    staff_view = staff_client.get("/api/sales").json()["items"]
    assert all(s["profit"] is None for s in staff_view)
    assert owner_client.get("/api/sales/999").status_code == 404
    # A sale closes opening stock for the product
    assert owner_client.get(f"/api/products/{stock['charger']['id']}").json()["opening_stock_open"] is False


def test_sales_need_login(client):
    assert client.get("/api/sales").status_code == 401
    assert client.get("/api/pos/search", params={"q": "x"}).status_code == 401
    assert client.post("/api/sales", json={}).status_code == 401
