"""Dashboard and reports: every figure checked against a day worked out by hand."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.clock import business_today
from tests.test_products import make_product, opening
from tests.test_units import imei, phone

TODAY = business_today()
T = TODAY.isoformat()


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture
def day(owner_client, staff_client):
    """One day of trading. Figures (₦):

    Sale A (staff, walk-in): 2 chargers at 6,500 less 100 discount = 12,900, + phone 420,000
            → 432,900 paid cash. Cost 2 × 4,000 + 380,000 = 388,000.
    Sale B (owner, Aisha, credit): 1 charger 6,500, 1,500 paid by bank transfer. Cost 4,000.
    Sale C (owner, walk-in): 1 charger 6,500 cash — then reversed, 6,500 refunded in cash.
    Return from A: 1 charger, value 12,900 ÷ 2 = 6,450 refunded in cash; its cost 4,000 comes back.
    Aisha later pays 2,000 cash.  One charger written off as damaged (cost 4,000).
    Rent 10,000 cash; a 500 expense recorded then reversed.
    Bought 5 cables at 1,000 from a supplier, paid 3,000 cash.
    """
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    a54 = phone(owner_client, name="Galaxy A54", selling_price="420000")
    units = owner_client.post(f"/api/products/{a54['id']}/units/opening", json={"units": [
        {"imei1": imei(1), "unit_cost": "380000"}, {"imei1": imei(2), "unit_cost": "380000"}]}).json()["units"]
    cable = make_product(owner_client, name="Cable", selling_price="1500")
    aisha = staff_client.post("/api/customers", json={"name": "Aisha Bello"}).json()

    def sell(client, lines, payments, **extra):
        r = client.post("/api/sales", json={"lines": lines, "payments": payments, "idempotency_key": key(), **extra})
        assert r.status_code == 201, r.text
        return r.json()

    a = sell(staff_client, [{"product_id": charger["id"], "quantity": 2, "discount": "100"},
                            {"product_id": a54["id"], "unit_id": units[0]["id"]}],
             [{"method_id": 1, "amount": "432900"}])
    sell(owner_client, [{"product_id": charger["id"]}], [{"method_id": 2, "amount": "1500"}], customer_id=aisha["id"])
    c = sell(owner_client, [{"product_id": charger["id"]}], [{"method_id": 1, "amount": "6500"}])
    assert owner_client.post(f"/api/sales/{c['id']}/reverse",
                             json={"reason": "Keyed twice", "refund_method_id": 1}).status_code == 200
    assert owner_client.post(f"/api/sales/{a['id']}/returns", json={
        "lines": [{"sale_line_id": a["lines"][0]["id"]}], "reason": "Faulty",
        "refund_method_id": 1, "idempotency_key": key()}).status_code == 201
    assert staff_client.post("/api/customer-payments", json={"customer_id": aisha["id"], "amount": "2000",
                                                             "method_id": 1, "idempotency_key": key()}).status_code == 201
    assert owner_client.post("/api/stock-adjustments", json={
        "lines": [{"product_id": charger["id"], "quantity_change": -1, "kind": "damage"}],
        "reason": "Dropped", "approve_now": True}).status_code == 201
    assert owner_client.post("/api/expenses", json={"category": "Rent", "amount": "10000", "method_id": 1}).status_code == 201
    wrong = owner_client.post("/api/expenses", json={"category": "Transport", "amount": "500"}).json()
    assert owner_client.post(f"/api/expenses/{wrong['id']}/reverse", json={"reason": "Duplicate"}).status_code == 200
    supplier = owner_client.post("/api/suppliers", json={"name": "Cable Co"}).json()
    p = owner_client.post("/api/purchases", json={"supplier_id": supplier["id"], "purchase_date": T, "lines": [
        {"product_id": cable["id"], "quantity": 5, "unit_cost": "1000"}]}).json()
    owner_client.post(f"/api/purchases/{p['id']}/post")
    assert owner_client.post("/api/supplier-payments", json={"supplier_id": supplier["id"], "amount": "3000",
                                                             "method_id": 1, "idempotency_key": key()}).status_code == 201
    return {"charger": charger, "phone": a54, "cable": cable, "aisha": aisha}


def test_dashboard_figures_for_the_day(owner_client, day):
    d = owner_client.get("/api/dashboard", params={"date_from": T, "date_to": T}).json()
    f = d["period"]
    assert f["sales_count"] == 2 and f["items_sold"] == 3  # 3 sold in A + 1 in B − 1 returned
    assert f["gross_sales"] == "439400.00" and f["discounts"] == "100.00"
    assert f["returns"] == "6450.00" and f["net_sales"] == "432950.00"
    assert f["cost_of_goods"] == "388000.00" and f["gross_profit"] == "44950.00"
    assert f["expenses"] == "10000.00" and f["expenses_recorded"] is True
    assert f["stock_losses"] == "4000.00" and f["net_profit"] == "30950.00"
    assert f["credit_given"] == "5000.00" and f["average_sale"] == "219700.00"
    assert f["cash_received"] == "442900.00" and f["refunds"] == "12950.00"
    assert {m["method"]: m["amount"] for m in f["received_by_method"]} == {
        "Cash": "441400.00", "Bank transfer": "1500.00"}

    # Right-now positions: charger 7 × 4,000 + phone 380,000 + cables 5 × 1,000
    assert d["stock_value"] == "413000.00" and d["retail_value"] == "473000.00"
    assert d["customers_owe"] == "3000.00" and d["owed_to_suppliers"] == "2000.00"
    assert [(b["name"], b["quantity"]) for b in d["best_sellers"]] == [("Charger", 2), ("Galaxy A54", 1)]
    assert d["by_day"] == [{"day": T, "net_sales": "432950.00", "gross_profit": "44950.00"}]
    assert d["pending_adjustments"] == 0 and d["recent_activity"]


def test_staff_dashboard_has_no_cost_or_profit(staff_client, day):
    d = staff_client.get("/api/dashboard").json()
    assert d["period"]["net_sales"] == "432950.00"
    for k in ("cost_of_goods", "gross_profit", "net_profit", "cash_received"):
        assert d["period"][k] is None
    assert d["stock_value"] is None and d["customers_owe"] is None and d["recent_activity"] is None
    assert d["by_day"][0]["gross_profit"] is None


def test_other_days_are_empty(owner_client, day):
    y = (TODAY - timedelta(days=1)).isoformat()
    f = owner_client.get("/api/dashboard", params={"date_from": y, "date_to": y}).json()["period"]
    assert f["sales_count"] == 0 and f["net_sales"] == "0.00" and f["expenses_recorded"] is False
    assert owner_client.get("/api/dashboard", params={"date_from": T, "date_to": y}).status_code == 422
    far = (TODAY - timedelta(days=400)).isoformat()
    assert owner_client.get("/api/dashboard", params={"date_from": far}).status_code == 422


def test_sales_and_profit_reports(owner_client, day):
    sales = owner_client.get("/api/reports/sales", params={"date_from": T, "date_to": T}).json()
    assert len(sales["rows"]) == 1
    row = sales["rows"][0]
    assert row["net_sales"] == "432950.00" and row["gross_profit"] == "44950.00" and row["margin"] == "10.4"

    pl = {r["line"].strip(): r["amount"] for r in
          owner_client.get("/api/reports/profit", params={"date_from": T, "date_to": T}).json()["rows"]}
    assert pl["Net sales"] == "432950.00" and pl["less Rent"] == "-10000.00"
    assert pl["less stock losses (damaged / missing)"] == "-4000.00" and pl["Net profit"] == "30950.00"
    assert "less Transport" not in pl  # the reversed expense does not count

    # With no expenses in a period, profit is labelled honestly
    y = (TODAY - timedelta(days=1)).isoformat()
    lines = [r["line"] for r in owner_client.get("/api/reports/profit", params={"date_from": y, "date_to": y}).json()["rows"]]
    assert "Profit before expenses (no expenses recorded)" in lines


def test_product_reports(owner_client, day):
    best = owner_client.get("/api/reports/best-sellers", params={"date_from": T, "date_to": T}).json()["rows"]
    assert [(r["product"], r["qty_sold"], r["net_sales"], r["gross_profit"]) for r in best] == [
        ("Charger", 2, "12950.00", "4950.00"), ("Galaxy A54", 1, "420000.00", "40000.00")]
    slow = owner_client.get("/api/reports/slow-sellers", params={"date_from": T, "date_to": T}).json()["rows"]
    assert slow[0]["product"] == "Cable" and slow[0]["qty_sold"] == 0  # in stock, never sold


def test_stock_debt_and_supplier_reports(owner_client, day):
    stock = owner_client.get("/api/reports/stock").json()
    by_name = {r["product"]: r for r in stock["rows"]}
    assert by_name["Charger"]["qty_on_hand"] == 7 and by_name["Charger"]["stock_value"] == "28000.00"
    assert by_name["Galaxy A54"]["stock_value"] == "380000.00" and by_name["Galaxy A54"]["status"] == "Low"
    assert stock["totals"]["stock_value"] == "413000.00"
    low = owner_client.get("/api/reports/stock", params={"stock": "low"}).json()["rows"]
    assert [r["product"] for r in low] == ["Galaxy A54"]

    debts = owner_client.get("/api/reports/debts").json()
    assert [(r["customer"], r["owed"]) for r in debts["rows"]] == [("Aisha Bello", "3000.00")]
    sup = owner_client.get("/api/reports/suppliers").json()["rows"]
    assert [(r["supplier"], r["bought"], r["paid"], r["balance"]) for r in sup] == [
        ("Cable Co", "5000.00", "3000.00", "2000.00")]
    purchases = owner_client.get("/api/reports/purchases", params={"date_from": T, "date_to": T}).json()
    assert purchases["rows"][0]["balance"] == "5000.00"  # paid on account, not against this purchase


def test_employee_corrections_cash_and_movements_reports(owner_client, day):
    emp = {r["employee"]: r for r in owner_client.get("/api/reports/employees", params={"date_from": T, "date_to": T}).json()["rows"]}
    assert emp["Amina Cashier"]["net_sales"] == "426450.00" and emp["Amina Cashier"]["gross_profit"] == "42450.00"
    assert emp["Amina Cashier"]["discounts"] == "100.00"
    assert emp["Jalal Owner"]["sales"] == 1 and emp["Jalal Owner"]["gross_profit"] == "2500.00"

    corr = owner_client.get("/api/reports/corrections", params={"date_from": T, "date_to": T}).json()["rows"]
    kinds = sorted((r["kind"], r["amount"]) for r in corr)
    assert kinds == [("Customer return", "6450.00"), ("Discount", "100.00"),
                     ("Expense reversed", "500.00"), ("Sale reversed", "6500.00")]

    cash = {r["method"]: r for r in owner_client.get("/api/reports/cash", params={"date_from": T, "date_to": T}).json()["rows"]}
    assert cash["Cash"]["received"] == "441400.00" and cash["Cash"]["refunds"] == "12950.00"
    assert cash["Cash"]["suppliers"] == "3000.00" and cash["Cash"]["expenses"] == "10000.00"
    assert cash["Cash"]["net"] == "415450.00" and cash["Bank transfer"]["net"] == "1500.00"

    moves = owner_client.get("/api/reports/movements", params={"date_from": T, "date_to": T,
                                                               "movement_type": "damage"}).json()["rows"]
    assert [(m["product"], m["change"], m["value"]) for m in moves] == [("Charger", -1, "-4000.00")]


def test_csv_export(owner_client, day):
    r = owner_client.get("/api/reports/sales", params={"date_from": T, "date_to": T, "format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert f'jdtechshop-sales-{TODAY:%Y%m%d}.csv' in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig").splitlines()
    assert text[1].startswith("Day,Sales,Items,Gross sales")
    assert text[2].split(",")[:4] == [T, "2", "3", "439400.00"]
    assert text[3].startswith("Total,2,3,439400.00")


def test_default_period_is_this_month(owner_client, day):
    r = owner_client.get("/api/reports/sales").json()
    assert r["rows"] and r["rows"][-1]["day"] == T


def test_reports_are_owner_only(staff_client, client, day):
    for name in ("sales", "profit", "stock", "debts", "cash"):
        assert staff_client.get(f"/api/reports/{name}").status_code == 403
        assert client.get(f"/api/reports/{name}").status_code == 401
    assert staff_client.get("/api/expenses").status_code == 403
    assert staff_client.post("/api/expenses", json={"category": "Rent", "amount": "1"}).status_code == 403
    assert staff_client.get("/api/reports/nonsense").status_code in (403, 422)


def test_expenses(owner_client):
    r = owner_client.post("/api/expenses", json={"category": "  Electricity &  fuel ", "amount": "2500.5",
                                                 "note": "Generator diesel"})
    assert r.status_code == 201 and r.json()["category"] == "Electricity & fuel" and r.json()["amount"] == "2500.50"
    assert r.json()["spent_on"] == T
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert owner_client.post("/api/expenses", json={"category": "Rent", "amount": "1", "spent_on": tomorrow}).status_code == 422
    assert owner_client.post("/api/expenses", json={"category": "Rent", "amount": "0"}).status_code == 422
    listing = owner_client.get("/api/expenses", params={"date_from": T, "date_to": T}).json()
    assert listing["total"] == 1 and listing["amount_total"] == "2500.50"
    cats = owner_client.get("/api/expenses/categories").json()
    assert cats[0] == "Electricity & fuel" and "Rent" in cats
    rev = owner_client.post(f"/api/expenses/{r.json()['id']}/reverse", json={"reason": "Wrong amount"})
    assert rev.json()["reversed"] is True
    assert owner_client.get("/api/expenses").json()["amount_total"] == "0"
    assert Decimal(owner_client.get("/api/expenses").json()["amount_total"]) == 0
