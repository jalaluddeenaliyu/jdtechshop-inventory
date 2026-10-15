"""Two (or more) people doing clashing things at the same moment.

Each test releases its requests together with a barrier, then checks the outcome is one the
shop could have reached one step at a time: nothing sold twice, stock never below zero,
money counted once, and the stock ledger still matching every quantity.
The simpler races (last charger, same phone, double click, same IMEI, overpaying) are in
test_sales, test_units, test_debts and test_purchasing.
"""
import threading
import uuid
from decimal import Decimal

from app.core.clock import business_today
from app.database import SessionLocal
from app.services import stock as stock_service
from tests.conftest import logged_in
from tests.test_products import make_product, opening
from tests.test_units import imei, phone

TODAY = business_today()
T = TODAY.isoformat()


def key() -> str:
    return uuid.uuid4().hex


def cash(amount: str) -> list[dict]:
    return [{"method_id": 1, "amount": amount}]


def at_once(*calls):
    """Each call is (username, path, json). All are logged in first, then posted together."""
    clients = [logged_in(user) for user, _, _ in calls]
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    errors: list[BaseException] = []

    def go(i):
        _, path, body = calls[i]
        barrier.wait()
        try:
            results[i] = clients[i].post(path, json=body)
        except BaseException as e:  # a server error: re-raised below so the test shows it
            errors.append(e)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(calls))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    if errors:
        raise errors[0]
    assert all(r is not None for r in results), "a request never finished (deadlock?)"
    return results


def codes(results) -> list[int]:
    return sorted(r.status_code for r in results)


def sale_body(lines, payments, **extra) -> dict:
    return {"lines": lines, "payments": payments, "idempotency_key": key(), **extra}


def qty(client, product_id) -> int:
    return client.get(f"/api/products/{product_id}").json()["qty_on_hand"]


def assert_ledger_matches():
    with SessionLocal() as db:
        assert stock_service.ledger_mismatches(db) == []


def test_eight_cashiers_five_chargers(owner_client, staff, owner):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 5, "4000")
    results = at_once(*[("cashier", "/api/sales", sale_body([{"product_id": charger["id"]}], cash("6500")))
                        for _ in range(8)])
    assert codes(results) == [201] * 5 + [409] * 3
    assert qty(owner_client, charger["id"]) == 0
    # Receipt numbers stay unique and without gaps, whoever got there first
    receipts = sorted(r.json()["receipt_no"] for r in results if r.status_code == 201)
    assert [int(n[-4:]) for n in receipts] == [1, 2, 3, 4, 5]
    assert_ledger_matches()


def test_carts_with_the_same_items_in_opposite_order_do_not_deadlock(owner_client, staff, owner):
    a = make_product(owner_client, name="Charger", selling_price="6500")
    b = make_product(owner_client, name="Cable", selling_price="1500")
    opening(owner_client, a["id"], 50, "4000")
    opening(owner_client, b["id"], 50, "700")
    ab = [{"product_id": a["id"]}, {"product_id": b["id"]}]
    results = at_once(*[("cashier", "/api/sales", sale_body(ab if i % 2 else ab[::-1], cash("8000")))
                        for i in range(6)])
    assert codes(results) == [201] * 6
    assert qty(owner_client, a["id"]) == 44 and qty(owner_client, b["id"]) == 44
    assert_ledger_matches()


def test_sale_and_approving_a_write_off_of_the_last_charger(owner_client, staff_client, staff, owner):
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 1, "4000")
    adj = staff_client.post("/api/stock-adjustments", json={
        "lines": [{"product_id": charger["id"], "quantity_change": -1, "kind": "missing"}],
        "reason": "Not on the shelf"}).json()
    sale, approval = at_once(
        ("cashier", "/api/sales", sale_body([{"product_id": charger["id"]}], cash("6500"))),
        ("owner", f"/api/stock-adjustments/{adj['id']}/approve", {}),
    )
    assert sorted([sale.status_code, approval.status_code]) in ([200, 409], [201, 409])
    assert qty(owner_client, charger["id"]) == 0  # gone once, never −1
    status = next(a["status"] for a in owner_client.get("/api/stock-adjustments").json()["items"] if a["id"] == adj["id"])
    assert status == ("approved" if approval.status_code == 200 else "pending")
    assert_ledger_matches()


def test_selling_a_phone_while_it_is_being_returned_to_the_supplier(owner_client, staff, owner):
    a54 = phone(owner_client, name="Galaxy A54", selling_price="420000")
    s = owner_client.post("/api/suppliers", json={"name": "Supplier"}).json()
    p = owner_client.post("/api/purchases", json={"supplier_id": s["id"], "purchase_date": T, "lines": [
        {"product_id": a54["id"], "quantity": 1, "unit_cost": "380000", "units": [{"imei1": imei(1)}]}]}).json()
    p = owner_client.post(f"/api/purchases/{p['id']}/post").json()
    unit = owner_client.get("/api/units", params={"product_id": a54["id"]}).json()["items"][0]
    sale, back = at_once(
        ("cashier", "/api/sales", sale_body([{"product_id": a54["id"], "unit_id": unit["id"]}], cash("420000"))),
        ("owner", f"/api/purchases/{p['id']}/returns", {
            "lines": [{"purchase_line_id": p["lines"][0]["id"], "quantity": 1, "unit_ids": [unit["id"]]}],
            "reason": "Faulty", "idempotency_key": key()}),
    )
    assert codes([sale, back]) == [201, 409]
    status = owner_client.get(f"/api/units/{unit['id']}").json()["status"]
    assert status == ("sold" if sale.status_code == 201 else "returned_to_supplier")
    assert qty(owner_client, a54["id"]) == 0
    # The supplier is credited only if the phone actually went back
    balance = owner_client.get(f"/api/suppliers/{s['id']}").json()["balance"]
    assert balance == ("380000.00" if sale.status_code == 201 else "0.00")
    assert_ledger_matches()


def test_two_owners_reverse_the_same_sale_at_once(owner_client, staff_client, owner):
    from tests.conftest import make_user
    make_user("owner2", "owner", "Second Owner")
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    sale = staff_client.post("/api/sales", json=sale_body([{"product_id": charger["id"], "quantity": 3}],
                                                          cash("19500"))).json()
    body = {"reason": "Keyed twice", "refund_method_id": 1}
    results = at_once(("owner", f"/api/sales/{sale['id']}/reverse", body),
                      ("owner2", f"/api/sales/{sale['id']}/reverse", body))
    assert codes(results) == [200, 200]  # the second sees it is already reversed
    assert qty(owner_client, charger["id"]) == 10  # back once, not twice
    d = owner_client.get("/api/dashboard", params={"date_from": T, "date_to": T}).json()["period"]
    assert d["refunds"] == "19500.00"  # refunded once
    assert len(owner_client.get("/api/audit-logs", params={"action": "sale.reversed"}).json()["items"]) == 1
    assert_ledger_matches()


def test_return_and_reversal_of_the_same_sale_at_once(owner_client, staff_client, owner):
    from tests.conftest import make_user
    make_user("owner2", "owner", "Second Owner")
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    sale = staff_client.post("/api/sales", json=sale_body([{"product_id": charger["id"], "quantity": 2}],
                                                          cash("13000"))).json()
    ret, rev = at_once(
        ("owner", f"/api/sales/{sale['id']}/returns", {
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "quantity": 1}], "reason": "Faulty",
            "refund_method_id": 1, "idempotency_key": key()}),
        ("owner2", f"/api/sales/{sale['id']}/reverse", {"reason": "Mistake", "refund_method_id": 1}),
    )
    assert codes([ret, rev]) in ([200, 409], [201, 409])  # one or the other, never both
    assert qty(owner_client, charger["id"]) == (9 if ret.status_code == 201 else 10)
    d = owner_client.get("/api/dashboard", params={"date_from": T, "date_to": T}).json()["period"]
    assert d["refunds"] == ("6500.00" if ret.status_code == 201 else "13000.00")
    assert_ledger_matches()


def test_two_returns_of_the_last_item_on_a_line(owner_client, staff_client, owner):
    from tests.conftest import make_user
    make_user("owner2", "owner", "Second Owner")
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    sale = staff_client.post("/api/sales", json=sale_body([{"product_id": charger["id"]}], cash("6500"))).json()

    def body():
        return {"lines": [{"sale_line_id": sale["lines"][0]["id"]}], "reason": "Faulty",
                "refund_method_id": 1, "idempotency_key": key()}
    results = at_once(("owner", f"/api/sales/{sale['id']}/returns", body()),
                      ("owner2", f"/api/sales/{sale['id']}/returns", body()))
    assert codes(results) == [201, 409]
    assert qty(owner_client, charger["id"]) == 10
    d = owner_client.get("/api/dashboard", params={"date_from": T, "date_to": T}).json()["period"]
    assert d["refunds"] == "6500.00" and d["net_sales"] == "0.00"
    assert_ledger_matches()


def test_repayment_and_return_clearing_the_same_debt(owner_client, staff_client, staff, owner):
    """Aisha owes ₦6,500 for a charger. She pays it while the owner books its return.
    Either order is fine, but she must end up owing nothing and get back exactly what she paid.
    Repeated a few times, because the clash only shows when both read the debt together."""
    charger = make_product(owner_client, name="Charger", selling_price="6500")
    opening(owner_client, charger["id"], 10, "4000")
    aisha = staff_client.post("/api/customers", json={"name": "Aisha Bello"}).json()
    paid = refunded = Decimal(0)
    for _ in range(6):
        sale = staff_client.post("/api/sales", json=sale_body([{"product_id": charger["id"]}], [],
                                                              customer_id=aisha["id"])).json()
        pay, ret = at_once(
            ("cashier", "/api/customer-payments", {"customer_id": aisha["id"], "amount": "6500", "method_id": 1,
                                                   "idempotency_key": key()}),
            ("owner", f"/api/sales/{sale['id']}/returns", {
                "lines": [{"sale_line_id": sale["lines"][0]["id"]}], "reason": "Changed mind",
                "refund_method_id": 1, "idempotency_key": key()}),
        )
        assert ret.status_code == 201, ret.text
        refund = Decimal(ret.json()["returns"][0]["refund_amount"])
        if pay.status_code == 201:  # paid first: the return refunds the ₦6,500
            assert refund == Decimal("6500.00")
            paid += Decimal("6500")
        else:  # returned first: nothing is owed any more, so the payment is refused
            assert pay.status_code == 409 and refund == 0
        refunded += refund
        assert owner_client.get(f"/api/customers/{aisha['id']}").json()["owed"] == "0.00"
    d = owner_client.get("/api/dashboard", params={"date_from": T, "date_to": T}).json()["period"]
    assert Decimal(d["cash_received"]) == paid and Decimal(d["refunds"]) == refunded == paid
    assert qty(owner_client, charger["id"]) == 10
    assert_ledger_matches()


def _bought(owner_client) -> tuple[dict, dict, dict]:
    n = key()[:6]
    charger = make_product(owner_client, name=f"Charger {n}", selling_price="6500")
    s = owner_client.post("/api/suppliers", json={"name": f"Supplier {n}"}).json()
    p = owner_client.post("/api/purchases", json={"supplier_id": s["id"], "purchase_date": T, "lines": [
        {"product_id": charger["id"], "quantity": 10, "unit_cost": "4000"}]}).json()
    return charger, s, owner_client.post(f"/api/purchases/{p['id']}/post").json()


def test_paying_a_supplier_while_goods_go_back(owner_client, owner):
    """Both change what the shop owes the supplier; each must see the other's effect."""
    from tests.conftest import make_user
    make_user("owner2", "owner", "Second Owner")
    for _ in range(4):
        charger, s, p = _bought(owner_client)
        pay, back = at_once(
            ("owner", "/api/supplier-payments", {"supplier_id": s["id"], "purchase_id": p["id"], "amount": "40000",
                                                 "method_id": 1, "idempotency_key": key()}),
            ("owner2", f"/api/purchases/{p['id']}/returns", {
                "lines": [{"purchase_line_id": p["lines"][0]["id"], "quantity": 2}], "reason": "Faulty",
                "idempotency_key": key()}),
        )
        assert back.status_code == 201, back.text
        balance = owner_client.get(f"/api/suppliers/{s['id']}").json()["balance"]
        if pay.status_code == 201:
            assert balance == "-8000.00"  # paid in full, then ₦8,000 of goods went back: the supplier owes the shop
        else:  # returned first: only ₦32,000 was owed, so ₦40,000 is an overpayment
            assert pay.status_code == 409 and balance == "32000.00"
        assert qty(owner_client, charger["id"]) == 8
    assert_ledger_matches()


def test_paying_a_supplier_while_the_purchase_is_reversed(owner_client, owner):
    from tests.conftest import make_user
    make_user("owner2", "owner", "Second Owner")
    for _ in range(4):
        charger, s, p = _bought(owner_client)
        pay, rev = at_once(
            ("owner", "/api/supplier-payments", {"supplier_id": s["id"], "purchase_id": p["id"], "amount": "1000",
                                                 "method_id": 1, "idempotency_key": key()}),
            ("owner2", f"/api/purchases/{p['id']}/reverse", {"reason": "Entered twice"}),
        )
        assert codes([pay, rev]) in ([201, 409], [200, 409], [201, 422], [200, 422]), (pay.text, rev.text)
        balance = owner_client.get(f"/api/suppliers/{s['id']}").json()["balance"]
        if pay.status_code == 201:  # paid first: a paid purchase cannot be reversed
            assert balance == "39000.00" and qty(owner_client, charger["id"]) == 10
        else:  # reversed first: nothing is owed for it, so no payment against it
            assert balance == "0.00" and qty(owner_client, charger["id"]) == 0
    assert_ledger_matches()
