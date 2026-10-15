"""The 15 acceptance tests, one per test, each a shop scenario through the real API.

The list is kept in docs/CHECKLIST.md. Many rules are also tested in detail elsewhere;
these tests check that a whole job works end to end, the way the shop does it.
Tests 14 and 15 run the real server and the real backup scripts.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.config import BACKEND_DIR
from app.core.clock import business_today
from app.database import engine
from tests.conftest import PASSWORD, settings
from tests.test_products import make_product, opening
from tests.test_units import imei, phone

TODAY = business_today()
T = TODAY.isoformat()
ROOT = BACKEND_DIR.parent
PG_BIN = "/Applications/Postgres.app/Contents/Versions/latest/bin"


def key() -> str:
    return uuid.uuid4().hex


def ok(r, status=201) -> dict:
    assert r.status_code == status, r.text
    return r.json()


def sell(client, lines, payments=(), **extra):
    return client.post("/api/sales", json={"lines": lines, "payments": list(payments),
                                           "idempotency_key": extra.pop("idempotency_key", key()), **extra})


def cash(amount: str) -> list[dict]:
    return [{"method_id": 1, "amount": amount}]


def product(client, product_id: int) -> dict:
    return client.get(f"/api/products/{product_id}").json()


def buy(client, lines: list[dict], supplier_name="Slot Distribution Ltd") -> dict:
    s = ok(client.post("/api/suppliers", json={"name": supplier_name}))
    p = ok(client.post("/api/purchases", json={"supplier_id": s["id"], "purchase_date": T, "lines": lines}))
    return ok(client.post(f"/api/purchases/{p['id']}/post"), 200)


def ledger_matches() -> bool:
    from app.database import SessionLocal
    from app.services import stock as stock_service
    with SessionLocal() as db:
        return stock_service.ledger_mismatches(db) == []


# 1 ---------------------------------------------------------------------------------------------
def test_at01_receiving_stock_updates_quantity_and_average_cost(owner_client):
    charger = make_product(owner_client, name="Oraimo 20W charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    a54 = phone(owner_client, name="Galaxy A54")
    p = buy(owner_client, [
        {"product_id": charger["id"], "quantity": 10, "unit_cost": "5000"},
        {"product_id": a54["id"], "quantity": 2, "unit_cost": "380000",
         "units": [{"imei1": imei(1)}, {"imei1": imei(2)}]},
    ])
    assert p["status"] == "posted"
    c = product(owner_client, charger["id"])
    assert c["qty_on_hand"] == 20 and c["avg_cost"] == "4500.0000"  # (10 × 4,000 + 10 × 5,000) ÷ 20
    units = owner_client.get("/api/units", params={"product_id": a54["id"]}).json()["items"]
    assert sorted(u["imei1"] for u in units) == [imei(1), imei(2)]
    assert {u["unit_cost"] for u in units} == {"380000.0000"} and {u["status"] for u in units} == {"in_stock"}
    assert product(owner_client, a54["id"])["qty_on_hand"] == 2
    # The supplier is owed for it, and the ledger shows the receipt
    assert owner_client.get(f"/api/suppliers/{p['supplier_id']}").json()["balance"] == "810000.00"
    moves = owner_client.get("/api/stock-movements", params={"product_id": charger["id"]}).json()["items"]
    assert sorted(m["movement_type"] for m in moves) == ["opening", "purchase"]
    assert ledger_matches()


# 2 ---------------------------------------------------------------------------------------------
def test_at02_imei_units_are_registered_and_duplicates_refused_in_either_slot(owner_client):
    a54 = phone(owner_client, name="Galaxy A54")
    ok(owner_client.post(f"/api/products/{a54['id']}/units/opening", json={"units": [
        {"imei1": imei(1), "imei2": imei(2), "serial_number": "r58x1", "unit_cost": "380000"}]}))
    s24 = phone(owner_client, name="Galaxy S24")
    for clash in ({"imei1": imei(2)}, {"imei1": imei(3), "imei2": imei(1)}, {"imei1": imei(4), "serial_number": "R58X1"}):
        r = owner_client.post(f"/api/products/{s24['id']}/units/opening", json={"units": [{**clash, "unit_cost": "1"}]})
        assert r.status_code == 409, clash
    # The same clash in a purchase is refused too, and nothing is half-saved
    s = ok(owner_client.post("/api/suppliers", json={"name": "Supplier"}))
    r = owner_client.post("/api/purchases", json={"supplier_id": s["id"], "purchase_date": T, "lines": [
        {"product_id": s24["id"], "quantity": 1, "unit_cost": "1", "units": [{"imei1": imei(1)}]}]})
    assert r.status_code in (409, 422)
    # A wrong check digit is caught before it is ever stored
    bad = imei(5)[:-1] + str((int(imei(5)[-1]) + 1) % 10)
    assert owner_client.post(f"/api/products/{s24['id']}/units/opening",
                             json={"units": [{"imei1": bad, "unit_cost": "1"}]}).status_code == 422
    assert owner_client.get("/api/units").json()["total"] == 1
    # Found by any part of the IMEI
    assert owner_client.get("/api/units", params={"q": imei(2)[-6:]}).json()["total"] == 1


# 3 ---------------------------------------------------------------------------------------------
def test_at03_selling_a_quantity_item_lowers_stock_through_the_ledger(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    sale = ok(sell(staff_client, [{"product_id": charger["id"], "quantity": 3}], cash("19500")))
    assert sale["total"] == "19500.00" and sale["status"] == "completed"
    assert product(owner_client, charger["id"])["qty_on_hand"] == 7
    moves = owner_client.get("/api/stock-movements", params={"product_id": charger["id"],
                                                             "movement_type": "sale"}).json()["items"]
    assert len(moves) == 1 and moves[0]["quantity_change"] == -3
    assert ledger_matches()
    # Staff see the price and stock, never the cost
    seen_by_staff = product(staff_client, charger["id"])
    assert seen_by_staff["qty_on_hand"] == 7 and seen_by_staff.get("avg_cost") is None


# 4 ---------------------------------------------------------------------------------------------
def test_at04_a_phone_sold_by_imei_cannot_be_sold_again(owner_client, staff_client):
    a54 = phone(owner_client, name="Galaxy A54", selling_price="420000")
    units = ok(owner_client.post(f"/api/products/{a54['id']}/units/opening", json={"units": [
        {"imei1": imei(1), "unit_cost": "380000"}, {"imei1": imei(2), "unit_cost": "375000"}]}))["units"]
    # The till finds the exact phone by its IMEI
    found = staff_client.get("/api/pos/search", params={"q": imei(2)}).json()
    assert [(f["kind"], f["unit"]["id"]) for f in found] == [("unit", units[1]["id"])]
    sale = ok(sell(staff_client, [{"product_id": a54["id"], "unit_id": units[1]["id"]}], cash("420000")))
    assert owner_client.get(f"/api/units/{units[1]['id']}").json()["status"] == "sold"
    assert owner_client.get(f"/api/sales/{sale['id']}").json()["profit"] == "45000.00"  # that unit's own cost
    r = sell(staff_client, [{"product_id": a54["id"], "unit_id": units[1]["id"]}], cash("420000"))
    assert r.status_code == 409 and "already sold" in r.json()["detail"]
    assert product(owner_client, a54["id"])["qty_on_hand"] == 1
    # A phone cannot be sold "by quantity" without saying which one
    assert sell(staff_client, [{"product_id": a54["id"], "quantity": 1}], cash("420000")).status_code in (409, 422)


# 5 ---------------------------------------------------------------------------------------------
def test_at05_stock_can_never_go_below_zero(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 2, "4000")
    r = sell(staff_client, [{"product_id": charger["id"], "quantity": 3}], cash("19500"))
    assert r.status_code == 409 and "2 available, 3 needed" in r.json()["detail"]
    ok(sell(staff_client, [{"product_id": charger["id"], "quantity": 2}], cash("13000")))
    assert sell(staff_client, [{"product_id": charger["id"]}], cash("6500")).status_code == 409
    # Not by an adjustment either
    r = owner_client.post("/api/stock-adjustments", json={
        "lines": [{"product_id": charger["id"], "quantity_change": -1, "kind": "missing"}],
        "reason": "Count", "approve_now": True})
    assert r.status_code == 409
    # And not even by SQL that skips the application: the database refuses
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE products SET qty_on_hand = -1 WHERE id = :id"), {"id": charger["id"]})
    assert product(owner_client, charger["id"])["qty_on_hand"] == 0


# 6 ---------------------------------------------------------------------------------------------
def test_at06_profit_uses_the_cost_frozen_at_the_time_of_sale(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    sale = ok(sell(staff_client, [{"product_id": charger["id"], "quantity": 2}], cash("13000")))
    assert owner_client.get(f"/api/sales/{sale['id']}").json()["profit"] == "5000.00"
    buy(owner_client, [{"product_id": charger["id"], "quantity": 8, "unit_cost": "6000"}])  # average → 5,000
    owner_client.patch(f"/api/products/{charger['id']}", json={"selling_price": "9000"})
    assert owner_client.get(f"/api/sales/{sale['id']}").json()["profit"] == "5000.00"
    later = ok(sell(owner_client, [{"product_id": charger["id"]}], cash("9000")))
    assert owner_client.get(f"/api/sales/{later['id']}").json()["profit"] == "4000.00"
    row = owner_client.get("/api/reports/sales", params={"date_from": T, "date_to": T}).json()["rows"][0]
    assert row["gross_profit"] == "9000.00"  # 5,000 + 4,000: the old sale is not re-costed


# 7 ---------------------------------------------------------------------------------------------
def test_at07_staff_discounts_are_limited_by_settings(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    # Default limit is 5%: ₦325 on a ₦6,500 charger
    ok(sell(staff_client, [{"product_id": charger["id"], "discount": "325"}], cash("6175")))
    r = sell(staff_client, [{"product_id": charger["id"], "discount": "325.01"}], cash("6174.99"))
    assert r.status_code == 403 and "5" in r.json()["detail"]
    # The owner may give more, but never more than the price
    ok(sell(owner_client, [{"product_id": charger["id"], "discount": "2000"}], cash("4500")))
    assert sell(owner_client, [{"product_id": charger["id"], "discount": "6500.01"}]).status_code == 422
    # Raising the limit in Settings lets staff give more
    s = owner_client.get("/api/settings").json()
    ok(owner_client.put("/api/settings", json={**s, "staff_max_discount_percent": "10"}), 200)
    ok(sell(staff_client, [{"product_id": charger["id"], "discount": "650"}], cash("5850")))
    # Prices always come from the product, never from the till
    r = staff_client.post("/api/sales", json={"lines": [{"product_id": charger["id"], "unit_price": "1"}],
                                              "payments": cash("1"), "idempotency_key": key()})
    assert r.status_code == 422 or r.json()["total"] == "6500.00"
    rows = owner_client.get("/api/reports/corrections", params={"date_from": T, "date_to": T}).json()["rows"]
    assert sum(Decimal(r["amount"]) for r in rows if r["kind"] == "Discount") == Decimal("2975.00")


# 8 ---------------------------------------------------------------------------------------------
def test_at08_credit_sale_partial_repayment_and_no_overpayment(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    aisha = ok(staff_client.post("/api/customers", json={"name": "Aisha Bello", "phone": "08031234567"}))
    # Credit is only for a named customer, never the walk-in
    assert sell(staff_client, [{"product_id": charger["id"]}]).status_code == 422
    sale = ok(sell(staff_client, [{"product_id": charger["id"], "quantity": 2}], cash("3000"), customer_id=aisha["id"]))
    assert sale["balance_due"] == "10000.00"
    assert staff_client.get(f"/api/customers/{aisha['id']}").json()["owed"] == "10000.00"

    def pay(amount):
        return staff_client.post("/api/customer-payments", json={
            "customer_id": aisha["id"], "amount": amount, "method_id": 1, "idempotency_key": key()})
    ok(pay("4000"))
    assert staff_client.get(f"/api/customers/{aisha['id']}").json()["owed"] == "6000.00"
    r = pay("6000.01")
    assert r.status_code == 409 and "Overpayments are not allowed" in r.json()["detail"]
    ok(pay("6000"))
    assert staff_client.get(f"/api/customers/{aisha['id']}").json()["owed"] == "0.00"
    assert staff_client.get(f"/api/sales/{sale['id']}").json()["balance_due"] == "0.00"
    assert pay("1").status_code == 409
    assert owner_client.get("/api/reports/debts").json()["rows"] == []


# 9 ---------------------------------------------------------------------------------------------
def test_at09_customer_return_restocks_and_reduces_debt_before_refunding(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    aisha = ok(staff_client.post("/api/customers", json={"name": "Aisha Bello"}))
    sale = ok(sell(staff_client, [{"product_id": charger["id"], "quantity": 2}], cash("10000"), customer_id=aisha["id"]))
    assert staff_client.get(f"/api/customers/{aisha['id']}").json()["owed"] == "3000.00"

    def give_back(quantity, **extra):
        return owner_client.post(f"/api/sales/{sale['id']}/returns", json={
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "quantity": quantity}], "reason": "Faulty",
            "idempotency_key": key(), **extra})
    # Returning one charger (₦6,500): ₦3,000 clears the debt, ₦3,500 is refunded
    assert give_back(1).status_code == 422  # a refund is due, so the owner must say how it is paid
    ret = ok(give_back(1, refund_method_id=1))["returns"][0]
    assert ret["value_total"] == "6500.00" and ret["refund_amount"] == "3500.00"
    assert owner_client.get(f"/api/customers/{aisha['id']}").json()["owed"] == "0.00"
    assert product(owner_client, charger["id"])["qty_on_hand"] == 9
    assert owner_client.get(f"/api/sales/{sale['id']}").json()["profit"] == "2500.00"  # one charger's profit is undone
    # Cannot give back more than was sold
    assert give_back(2, refund_method_id=1).status_code in (409, 422)
    assert ledger_matches()


# 10 --------------------------------------------------------------------------------------------
def test_at10_reversing_a_sale_restores_stock_and_money_and_keeps_the_record(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    a54 = phone(owner_client, name="Galaxy A54", selling_price="420000")
    unit = ok(owner_client.post(f"/api/products/{a54['id']}/units/opening",
                                json={"units": [{"imei1": imei(1), "unit_cost": "380000"}]}))["units"][0]
    sale = ok(sell(staff_client, [{"product_id": charger["id"], "quantity": 2},
                                  {"product_id": a54["id"], "unit_id": unit["id"]}], cash("433000")))
    assert staff_client.post(f"/api/sales/{sale['id']}/reverse", json={"reason": "Wrong items"}).status_code == 403
    r = ok(owner_client.post(f"/api/sales/{sale['id']}/reverse",
                             json={"reason": "Keyed on the wrong till", "refund_method_id": 1}), 200)
    assert r["status"] == "reversed" and r["reversal_refund"] == "433000.00" and r["profit"] == "0.00"
    assert product(owner_client, charger["id"])["qty_on_hand"] == 10
    assert owner_client.get(f"/api/units/{unit['id']}").json()["status"] == "in_stock"
    # The sale is still there, marked reversed, with who and why
    kept = owner_client.get(f"/api/sales/{sale['id']}").json()
    assert kept["receipt_no"] == sale["receipt_no"] and kept["reversal_reason"] == "Keyed on the wrong till"
    log = owner_client.get("/api/audit-logs", params={"action": "sale.reversed"}).json()["items"]
    assert len(log) == 1 and log[0]["user_name"] == "Jalal Owner"
    # It no longer counts as a sale; the refund shows as money out
    d = owner_client.get("/api/dashboard", params={"date_from": T, "date_to": T}).json()["period"]
    assert d["net_sales"] == "0.00" and d["refunds"] == "433000.00"
    # Nothing can edit or delete it, even outside the application
    for sql in ("DELETE FROM sales", "UPDATE sales SET reversal_reason = 'edited'"):
        with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
            with engine.begin() as conn:
                conn.execute(text(sql))
    assert ledger_matches()


# 11 --------------------------------------------------------------------------------------------
def test_at11_submitting_the_same_sale_twice_records_it_once(owner_client, staff_client):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    k = key()
    first = sell(staff_client, [{"product_id": charger["id"], "quantity": 2}], cash("13000"), idempotency_key=k)
    again = sell(staff_client, [{"product_id": charger["id"], "quantity": 2}], cash("13000"), idempotency_key=k)
    assert (first.status_code, again.status_code) == (201, 200) and first.json()["id"] == again.json()["id"]
    assert product(owner_client, charger["id"])["qty_on_hand"] == 8
    assert owner_client.get("/api/sales").json()["total"] == 1
    # The same holds for a repayment
    aisha = ok(staff_client.post("/api/customers", json={"name": "Aisha"}))
    ok(sell(staff_client, [{"product_id": charger["id"]}], customer_id=aisha["id"]))
    body = {"customer_id": aisha["id"], "amount": "1000", "method_id": 1, "idempotency_key": key()}
    assert [staff_client.post("/api/customer-payments", json=body).status_code for _ in range(2)] == [201, 200]
    assert staff_client.get(f"/api/customers/{aisha['id']}").json()["owed"] == "5500.00"


# 12 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/users", {"full_name": "X", "username": "x", "password": PASSWORD, "role": "owner"}),
    ("put", "/api/settings", {"business_name": "X", "staff_max_discount_percent": "100"}),
    ("post", "/api/products", {"name": "X", "tracking_type": "quantity", "selling_price": "1"}),
    ("post", "/api/suppliers", {"name": "X"}),
    ("post", "/api/expenses", {"category": "Rent", "amount": "1"}),
    ("post", "/api/sales/1/reverse", {"reason": "x y z"}),
    ("post", "/api/stock-adjustments/1/approve", {}),
    ("get", "/api/reports/profit", None),
    ("get", "/api/audit-logs", None),
])
def test_at12_staff_cannot_do_owner_only_actions_through_the_api(staff_client, method, path, body):
    r = getattr(staff_client, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 403, (path, r.text)
    # The full list of owner-only endpoints is checked in test_permissions.py.


# 13 --------------------------------------------------------------------------------------------
def test_at13_dashboard_and_report_totals_match_the_records(owner_client, staff_client):
    """Do a busy day through the API, then recompute every headline straight from the tables."""
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 30, "4000")
    cable = make_product(owner_client, name="Cable", selling_price="1500")
    opening(owner_client, cable["id"], 20, "700")
    aisha = ok(staff_client.post("/api/customers", json={"name": "Aisha"}))
    sales = [
        ok(sell(staff_client, [{"product_id": charger["id"], "quantity": 3, "discount": "300"}], cash("19200"))),
        ok(sell(staff_client, [{"product_id": cable["id"], "quantity": 4}], [{"method_id": 2, "amount": "6000"}])),
        ok(sell(owner_client, [{"product_id": charger["id"]}, {"product_id": cable["id"], "quantity": 2}],
                cash("2000"), customer_id=aisha["id"])),
        ok(sell(owner_client, [{"product_id": cable["id"]}], cash("1500"))),
    ]
    ok(owner_client.post(f"/api/sales/{sales[3]['id']}/reverse", json={"reason": "Test", "refund_method_id": 1}), 200)
    ok(owner_client.post(f"/api/sales/{sales[1]['id']}/returns", json={
        "lines": [{"sale_line_id": sales[1]["lines"][0]["id"], "quantity": 1}], "reason": "Faulty",
        "refund_method_id": 2, "idempotency_key": key()}))
    ok(staff_client.post("/api/customer-payments", json={"customer_id": aisha["id"], "amount": "3000",
                                                         "method_id": 1, "idempotency_key": key()}))
    ok(owner_client.post("/api/expenses", json={"category": "Power", "amount": "4500", "method_id": 1}))

    def q(sql: str) -> Decimal:
        with engine.connect() as conn:
            return Decimal(conn.execute(text(sql), {"d": TODAY}).scalar() or 0)
    done = "FROM sales s JOIN sale_lines l ON l.sale_id = s.id WHERE s.status = 'completed' AND s.business_date = :d"
    gross = q(f"SELECT sum(l.line_total) {done}")  # gross sales are counted after discounts
    discounts = q(f"SELECT sum(l.discount) {done}")
    cost = q(f"SELECT sum(l.cost_total_snapshot) {done}")
    returns = q("SELECT sum(value_total) FROM sale_returns WHERE business_date = :d")
    returned_cost = q("SELECT sum(rl.cost_total) FROM sale_return_lines rl JOIN sale_returns r ON r.id = rl.return_id"
                      " WHERE r.business_date = :d")
    expenses = q("SELECT sum(amount) FROM expenses WHERE NOT reversed AND spent_on = :d")
    taken = q("SELECT sum(amount) FROM customer_payments WHERE business_date = :d")  # at the till and later
    refunds = q("SELECT sum(reversal_refund) FROM sales WHERE business_date = :d") \
        + q("SELECT sum(refund_amount) FROM sale_returns WHERE business_date = :d")
    named = "JOIN customers c ON c.id = {t}.customer_id AND NOT c.is_walk_in"
    owed = q(f"SELECT sum(s.total) FROM sales s {named.format(t='s')} WHERE s.status = 'completed'") \
        - q(f"SELECT sum(p.amount) FROM customer_payments p {named.format(t='p')} WHERE NOT p.reversed") \
        - q(f"SELECT sum(r.value_total - r.refund_amount) FROM sale_returns r {named.format(t='r')}")
    stock_value = q("SELECT sum(qty_on_hand * avg_cost) FROM products")

    net_sales = gross - returns
    gross_profit = net_sales - (cost - returned_cost)
    f = owner_client.get("/api/dashboard", params={"date_from": T, "date_to": T}).json()
    p = f["period"]
    money = lambda v: Decimal(v)  # noqa: E731
    assert money(p["gross_sales"]) == gross and money(p["discounts"]) == discounts
    assert money(p["returns"]) == returns and money(p["net_sales"]) == net_sales
    assert money(p["gross_profit"]) == gross_profit and money(p["expenses"]) == expenses
    assert money(p["net_profit"]) == gross_profit - expenses
    assert money(p["cash_received"]) == taken and money(p["refunds"]) == refunds
    assert money(f["customers_owe"]) == owed
    assert money(f["stock_value"]) == stock_value.quantize(Decimal("0.01"))
    # The reports agree with the dashboard and with each other
    day = owner_client.get("/api/reports/sales", params={"date_from": T, "date_to": T}).json()["totals"]
    assert money(day["net_sales"]) == net_sales and money(day["gross_profit"]) == gross_profit
    pl = {r["line"].strip(): money(r["amount"]) for r in
          owner_client.get("/api/reports/profit", params={"date_from": T, "date_to": T}).json()["rows"]}
    assert pl["Net sales"] == net_sales and pl["Net profit"] == gross_profit - expenses
    assert money(owner_client.get("/api/reports/debts").json()["totals"]["owed"]) == owed
    assert money(owner_client.get("/api/reports/stock").json()["totals"]["stock_value"]) == money(f["stock_value"])
    by_method = owner_client.get("/api/reports/cash", params={"date_from": T, "date_to": T}).json()["totals"]
    assert money(by_method["received"]) == taken and money(by_method["refunds"]) == refunds
    assert money(by_method["expenses"]) == expenses
    emp = owner_client.get("/api/reports/employees", params={"date_from": T, "date_to": T}).json()["totals"]
    assert money(emp["net_sales"]) == net_sales
    # And the CSV download carries the same figures
    csv = owner_client.get("/api/reports/sales", params={"date_from": T, "date_to": T, "format": "csv"}).text
    assert str(net_sales) in csv


# 14 --------------------------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    env = {**os.environ, "ENVIRONMENT": "test", "TEST_DATABASE_URL": settings.test_database_url}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise AssertionError(proc.stderr.read().decode())
        time.sleep(0.2)
    proc.kill()
    raise AssertionError("server did not start")


def test_at14_data_is_still_there_after_the_server_is_killed_and_restarted(owner):
    port = _free_port()
    base = f"http://127.0.0.1:{port}/api"
    headers = {"X-JDT-Client": "web"}
    server = _start_server(port)
    try:
        with httpx.Client(base_url=base, headers=headers) as c:
            ok(c.post("/auth/login", json={"username": "owner", "password": PASSWORD}), 200)
            charger = ok(c.post("/products", json={"name": "Charger", "tracking_type": "quantity", "selling_price": "6500"}))
            ok(c.post(f"/products/{charger['id']}/opening-stock", json={"quantity": 5, "unit_cost": "4000"}))
            sale = ok(c.post("/sales", json={"lines": [{"product_id": charger["id"], "quantity": 2}],
                                            "payments": cash("13000"), "idempotency_key": key()}))
            session_cookies = dict(c.cookies)
    finally:
        server.send_signal(signal.SIGKILL)  # like a power cut: no clean shutdown
        server.wait()

    server = _start_server(port)
    try:
        # The old login still works (sessions are stored), and everything is there
        with httpx.Client(base_url=base, headers=headers, cookies=session_cookies) as c:
            assert ok(c.get("/auth/me"), 200)["username"] == "owner"
            assert ok(c.get(f"/products/{charger['id']}"), 200)["qty_on_hand"] == 3
            kept = ok(c.get(f"/sales/{sale['id']}"), 200)
            assert kept["receipt_no"] == sale["receipt_no"] and kept["total"] == "13000.00"
            # Work carries on where it left off: the next receipt number follows on
            nxt = ok(c.post("/sales", json={"lines": [{"product_id": charger["id"]}],
                                           "payments": cash("6500"), "idempotency_key": key()}))
            assert int(nxt["receipt_no"][-4:]) == int(sale["receipt_no"][-4:]) + 1
    finally:
        server.terminate()
        server.wait(10)
    assert ledger_matches()


# 15 --------------------------------------------------------------------------------------------
def _pg_tools_env() -> dict:
    env = dict(os.environ)
    if not shutil.which("pg_dump") and Path(PG_BIN).exists():
        env["PATH"] = f"{PG_BIN}:{env['PATH']}"
    return env


TABLES = ["users", "products", "product_units", "stock_movements", "sales", "sale_lines",
          "customers", "customer_payments", "suppliers", "purchases", "audit_logs", "imei_registry"]


def _counts(url: str) -> dict:
    e = create_engine(url)
    try:
        with e.connect() as conn:
            return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES} | {
                "money": conn.execute(text("SELECT sum(total) FROM sales")).scalar(),
                "stock": conn.execute(text("SELECT sum(qty_on_hand) FROM products")).scalar()}
    finally:
        e.dispose()


def test_at15_backup_and_restore_works(owner_client, staff_client, tmp_path):
    env = _pg_tools_env()
    if not shutil.which("pg_dump", path=env["PATH"]):
        pytest.skip("pg_dump / pg_restore not installed")
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    a54 = phone(owner_client, name="Galaxy A54", selling_price="420000")
    unit = ok(owner_client.post(f"/api/products/{a54['id']}/units/opening",
                                json={"units": [{"imei1": imei(1), "unit_cost": "380000"}]}))["units"][0]
    aisha = ok(staff_client.post("/api/customers", json={"name": "Aisha"}))
    ok(sell(staff_client, [{"product_id": charger["id"], "quantity": 2},
                           {"product_id": a54["id"], "unit_id": unit["id"]}], cash("400000"), customer_id=aisha["id"]))
    ok(staff_client.post("/api/customer-payments", json={"customer_id": aisha["id"], "amount": "5000",
                                                         "method_id": 1, "idempotency_key": key()}))
    test_url = settings.test_database_url
    before = _counts(test_url)

    # Back up with the real script, into a folder of its own (the shop's backups/ is never touched)
    out = subprocess.run([str(ROOT / "scripts" / "backup.sh")], capture_output=True, text=True, cwd=ROOT,
                         env={**env, "DATABASE_URL": test_url, "BACKUP_DIR": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    dumps = list(tmp_path.glob("jdtechshop-*.dump"))
    assert len(dumps) == 1 and "Backup saved" in out.stdout

    # Restore into a brand-new database with the real script
    restore_db = f"jdtechshop_restore_check_{os.getpid()}"
    restore_url = test_url.rsplit("/", 1)[0] + "/" + restore_db
    admin = create_engine(test_url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{restore_db}"'))
        conn.execute(text(f'CREATE DATABASE "{restore_db}"'))
    try:
        # Without typing RESTORE nothing happens
        no = subprocess.run([str(ROOT / "scripts" / "restore.sh"), str(dumps[0]), restore_url],
                            input="no\n", capture_output=True, text=True, env=env)
        assert no.returncode != 0 and "Cancelled" in no.stdout
        out = subprocess.run([str(ROOT / "scripts" / "restore.sh"), str(dumps[0]), restore_url],
                             input="RESTORE\n", capture_output=True, text=True, env=env)
        assert out.returncode == 0, out.stderr
        after = _counts(restore_url)
        assert after == before and before["sales"] == 1 and before["stock"] == 8

        # The restored copy is a working shop: the health check passes against it
        check = subprocess.run([sys.executable, "-m", "app.cli", "check"], capture_output=True, text=True,
                               cwd=BACKEND_DIR, env={**env, "ENVIRONMENT": "development",
                                                     "DATABASE_URL": restore_url})
        assert check.returncode == 0, check.stdout + check.stderr
        assert "every product quantity matches" in check.stdout
        # …and its permanence rules came back with it
        e = create_engine(restore_url)
        try:
            with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
                with e.begin() as conn:
                    conn.execute(text("DELETE FROM sales"))
        finally:
            e.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{restore_db}" WITH (FORCE)'))
        admin.dispose()
