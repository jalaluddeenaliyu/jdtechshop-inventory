"""Swap deals: the customer's old phone taken in at the till as part payment."""
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models import CustomerPayment, ProductUnit, StockMovement
from app.schemas.units import luhn_ok


def imei(n: int) -> str:
    base = f"35693803{n:06d}"
    return next(base + str(d) for d in range(10) if luhn_ok(base + str(d)))


def cash(client):
    return client.get("/api/payment-methods").json()[0]["id"]


def phone_product(client, name, price, units=()):
    p = client.post("/api/products", json={"name": name, "tracking_type": "serialized",
                                           "selling_price": price}).json()
    if units:
        client.post(f"/api/products/{p['id']}/units/opening",
                    json={"units": [{"imei1": i, "unit_cost": c} for i, c in units]})
    return p["id"]


def unit_id(client, number):
    return client.get("/api/units", params={"q": number}).json()["items"][0]["id"]


def shop(client):
    new = phone_product(client, "iPhone 13 128GB", "650000", [(imei(1), "500000")])
    old = phone_product(client, "iPhone 11 64GB", "280000")
    return new, old


def sell(client, new_pid, unit, trade_in=None, paid="450000", key="s", customer_id=None):
    body = {"idempotency_key": key * 32, "customer_id": customer_id,
            "lines": [{"product_id": new_pid, "unit_id": unit}],
            "payments": [{"method_id": cash(client), "amount": paid}] if paid else []}
    if trade_in is not None:
        body["trade_in"] = trade_in
    return client.post("/api/sales", json=body)


def test_swap_takes_the_old_phone_in_as_part_payment(owner_client):
    new, old = shop(owner_client)
    r = sell(owner_client, new, unit_id(owner_client, imei(1)),
             {"product_id": old, "imei1": imei(2), "value": "200000", "condition": "used"})
    assert r.status_code == 201, r.text
    sale = r.json()
    assert sale["total"] == "650000.00" and sale["balance_due"] == "0.00"
    assert sale["amount_paid"] == "650000.00" and Decimal(sale["profit"]) == Decimal("150000")
    assert {p["method_name"]: p["amount"] for p in sale["payments"]} == \
        {"Cash": "450000.00", "Swap (old phone)": "200000.00"}
    t = sale["trade_in"]
    assert t["product_name"] == "iPhone 11 64GB" and t["imei1"] == imei(2) and t["value"] == "200000.00"
    unit = owner_client.get("/api/units", params={"q": imei(2)}).json()["items"][0]
    assert unit["status"] == "in_stock" and unit["condition"] == "used"
    assert Decimal(unit["unit_cost"]) == Decimal("200000")
    assert owner_client.get(f"/api/products/{old}").json()["qty_on_hand"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(StockMovement.movement_type).where(
            StockMovement.unit_id == unit["id"])) == "trade_in"
    # Cash figures count only money; the phone's value is shown apart.
    period = owner_client.get("/api/dashboard").json()["period"]
    assert period["cash_received"] == "450000.00" and period["swap_value"] == "200000.00"
    assert [m["method"] for m in period["received_by_method"]] == ["Cash"]
    cash_report = owner_client.get("/api/reports/cash").json()
    assert [r["method"] for r in cash_report["rows"]] == ["Cash"]
    assert any("swaps" in n and "200,000" in n for n in cash_report["notes"])
    # Selling the old phone later shows the real profit.
    r = owner_client.post("/api/sales", json={
        "idempotency_key": "t" * 32, "lines": [{"product_id": old, "unit_id": unit["id"]}],
        "payments": [{"method_id": cash(owner_client), "amount": "280000"}]})
    assert Decimal(r.json()["profit"]) == Decimal("80000")


def test_swap_can_add_a_new_product(owner_client):
    new, _ = shop(owner_client)
    r = sell(owner_client, new, unit_id(owner_client, imei(1)),
             {"new_product_name": "Samsung Galaxy A10", "new_product_price": "70000",
              "imei1": imei(3), "value": "50000"}, paid="600000")
    assert r.status_code == 201, r.text
    p = next(p for p in owner_client.get("/api/products", params={"q": "A10"}).json()["items"])
    assert p["tracking_type"] == "serialized" and p["qty_on_hand"] == 1 and p["selling_price"] == "70000.00"


def test_swap_rules(owner_client):
    new, old = shop(owner_client)
    uid = unit_id(owner_client, imei(1))
    swap = {"product_id": old, "imei1": imei(2), "value": "200000"}
    r = sell(owner_client, new, uid, {**swap, "value": "700000"}, paid=None)
    assert r.status_code == 422 and "more than this sale" in r.json()["detail"]
    r = sell(owner_client, new, uid, swap, paid="500000")
    assert r.status_code == 422 and "plus the other payments" in r.json()["detail"]
    r = sell(owner_client, new, uid, swap, paid="100000")
    assert r.status_code == 422 and "Walk-in" in r.json()["detail"]
    r = sell(owner_client, new, uid, {**swap, "imei1": imei(1)})  # that's the phone being sold
    assert r.status_code == 409 and "in stock" in r.json()["detail"]
    cable = owner_client.post("/api/products", json={"name": "Cable", "tracking_type": "quantity",
                                                     "selling_price": "500"}).json()["id"]
    r = sell(owner_client, new, uid, {**swap, "product_id": cable})
    assert r.status_code == 422 and "not a phone" in r.json()["detail"]
    r = sell(owner_client, new, uid, {"product_id": old, "value": "1"})
    assert r.status_code == 422  # no IMEI/serial
    # The swap kind is never a normal payment method.
    methods = owner_client.get("/api/payment-methods", params={"include_inactive": True}).json()
    assert all("Swap" not in m["name"] for m in methods)
    with SessionLocal() as db:
        from app.models import PaymentMethod
        swap_id = db.scalar(select(PaymentMethod.id).where(PaymentMethod.is_trade_in.is_(True)))
    r = owner_client.post("/api/sales", json={
        "idempotency_key": "p" * 32, "lines": [{"product_id": new, "unit_id": uid}],
        "payments": [{"method_id": swap_id, "amount": "650000"}]})
    assert r.status_code == 422
    assert owner_client.patch(f"/api/payment-methods/{swap_id}", json={"is_active": True}).status_code == 404


def test_swap_on_credit_for_a_named_customer(owner_client):
    new, old = shop(owner_client)
    c = owner_client.post("/api/customers", json={"name": "Musa Ibrahim", "phone": "08031234567"}).json()
    r = sell(owner_client, new, unit_id(owner_client, imei(1)),
             {"product_id": old, "imei1": imei(2), "value": "200000"}, paid=None, customer_id=c["id"])
    assert r.status_code == 201, r.text
    assert r.json()["balance_due"] == "450000.00"
    assert owner_client.get(f"/api/customers/{c['id']}").json()["owed"] == "450000.00"


def test_reversing_a_swap_gives_the_phone_back(owner_client):
    new, old = shop(owner_client)
    sale = sell(owner_client, new, unit_id(owner_client, imei(1)),
                {"product_id": old, "imei1": imei(2), "value": "200000"}).json()
    r = owner_client.post(f"/api/sales/{sale['id']}/reverse",
                          json={"reason": "Customer changed mind", "refund_method_id": cash(owner_client)})
    assert r.status_code == 200, r.text
    rev = r.json()
    assert rev["reversal_refund"] == "450000.00"  # only the money; the phone goes back
    assert rev["trade_in"]["returned_to_customer"] is True and rev["trade_in"]["imei1"] == imei(2)
    assert owner_client.get(f"/api/products/{old}").json()["qty_on_hand"] == 0
    assert owner_client.get(f"/api/products/{new}").json()["qty_on_hand"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(CustomerPayment.reversed).where(CustomerPayment.amount == 200000)) is True
    assert owner_client.get("/api/dashboard").json()["period"]["swap_value"] == "0.00"
    # The same phone can be swapped in again later.
    again = sell(owner_client, new, unit_id(owner_client, imei(1)),
                 {"product_id": old, "imei1": imei(2), "value": "190000"}, paid="460000", key="a")
    assert again.status_code == 201, again.text


def test_cannot_reverse_once_the_old_phone_is_sold_on(owner_client):
    new, old = shop(owner_client)
    sale = sell(owner_client, new, unit_id(owner_client, imei(1)),
                {"product_id": old, "imei1": imei(2), "value": "200000"}).json()
    owner_client.post("/api/sales", json={
        "idempotency_key": "o" * 32, "lines": [{"product_id": old, "unit_id": unit_id(owner_client, imei(2))}],
        "payments": [{"method_id": cash(owner_client), "amount": "280000"}]})
    r = owner_client.post(f"/api/sales/{sale['id']}/reverse",
                          json={"reason": "Wrong phone", "refund_method_id": cash(owner_client)})
    assert r.status_code == 409 and "no longer in stock" in r.json()["detail"]


def test_a_phone_the_shop_sold_comes_back_in_a_swap(owner_client):
    new, old = shop(owner_client)
    tecno = phone_product(owner_client, "Tecno Spark 10", "120000", [(imei(5), "90000")])
    first = owner_client.post("/api/sales", json={
        "idempotency_key": "f" * 32, "lines": [{"product_id": tecno, "unit_id": unit_id(owner_client, imei(5))}],
        "payments": [{"method_id": cash(owner_client), "amount": "120000"}]}).json()
    # Months later the customer swaps it for the iPhone. Product picked doesn't matter: it's
    # the Tecno the shop sold.
    r = sell(owner_client, new, unit_id(owner_client, imei(1)),
             {"product_id": old, "imei1": imei(5), "value": "60000"}, paid="590000")
    assert r.status_code == 201, r.text
    swap_sale = r.json()
    assert swap_sale["trade_in"]["product_name"] == "Tecno Spark 10"
    unit = owner_client.get("/api/units", params={"q": imei(5)}).json()["items"][0]
    assert unit["status"] == "in_stock" and Decimal(unit["unit_cost"]) == Decimal("60000")
    assert owner_client.get(f"/api/products/{tecno}").json()["qty_on_hand"] == 1
    # The old receipt still shows it, and it can't also be returned there.
    old_receipt = owner_client.get(f"/api/sales/{first['id']}").json()
    assert old_receipt["lines"][0]["imei1"] == imei(5)
    r = owner_client.post(f"/api/sales/{first['id']}/returns", json={
        "lines": [{"sale_line_id": first["lines"][0]["id"]}], "reason": "Faulty",
        "refund_method_id": cash(owner_client), "idempotency_key": "r" * 32})
    assert r.status_code == 409 and "swap" in r.json()["detail"]
    # Undoing the swap puts the Tecno back as sold on the first receipt, at its old cost.
    r = owner_client.post(f"/api/sales/{swap_sale['id']}/reverse",
                          json={"reason": "Mistake", "refund_method_id": cash(owner_client)})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        u = db.get(ProductUnit, unit["id"])
        assert u.status == "sold" and u.sale_line_id == first["lines"][0]["id"]
        assert u.unit_cost == Decimal("90000") and u.imei1 == imei(5)
    assert owner_client.get(f"/api/products/{tecno}").json()["qty_on_hand"] == 0
    # A typed number that isn't that phone's is refused.
    r = sell(owner_client, new, unit_id(owner_client, imei(1)),
             {"product_id": old, "imei1": imei(5), "serial_number": "ABC12345", "value": "60000"},
             paid="590000", key="m")
    assert r.status_code == 409 and "don't match" in r.json()["detail"]
