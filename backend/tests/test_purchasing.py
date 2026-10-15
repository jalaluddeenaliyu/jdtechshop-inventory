"""Suppliers, purchases (draft → posted), landed and weighted-average cost, supplier payments."""
import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.clock import business_today
from app.database import engine
from app.services.purchasing import allocate_costs
from tests.conftest import logged_in
from tests.test_products import make_product, opening
from tests.test_units import imei, phone

TODAY = business_today()


def key() -> str:
    return uuid.uuid4().hex


def supplier(client, name="Slot Distribution Ltd") -> dict:
    r = client.post("/api/suppliers", json={"name": name, "phone": "0803 111 2222"})
    assert r.status_code == 201, r.text
    return r.json()


def draft(client, supplier_id: int, lines: list[dict], **extra) -> dict:
    body = {"supplier_id": supplier_id, "purchase_date": TODAY.isoformat(), "lines": lines, **extra}
    r = client.post("/api/purchases", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def post(client, purchase_id: int) -> dict:
    r = client.post(f"/api/purchases/{purchase_id}/post")
    assert r.status_code == 200, r.text
    return r.json()


def pay(client, supplier_id: int, amount: str, **extra):
    return client.post("/api/supplier-payments", json={
        "supplier_id": supplier_id, "amount": amount, "method_id": 1,
        "idempotency_key": extra.pop("idempotency_key", key()), **extra})


# ---------- Cost allocation ----------

def test_allocate_costs_by_value_with_exact_remainder():
    # 10 chargers at 4,000 (40,000) and 2 phones at 380,000 (760,000); ₦8,000 transport
    lines = allocate_costs([(10, Decimal("4000")), (2, Decimal("380000"))], Decimal("8000"))
    assert [c.allocated_extra_cost for c in lines] == [Decimal("400.00"), Decimal("7600.00")]
    assert [c.landed_unit_cost for c in lines] == [Decimal("4040.0000"), Decimal("383800.0000")]

    # Shares that do not divide evenly still add up exactly
    lines = allocate_costs([(1, Decimal("1")), (1, Decimal("1")), (1, Decimal("1"))], Decimal("100"))
    assert [c.allocated_extra_cost for c in lines] == [Decimal("33.33"), Decimal("33.33"),
                                                       Decimal("33.34")]
    # Free items: costs spread by quantity
    lines = allocate_costs([(1, Decimal("0")), (3, Decimal("0"))], Decimal("40"))
    assert [c.allocated_extra_cost for c in lines] == [Decimal("10.00"), Decimal("30.00")]
    assert lines[1].landed_unit_cost == Decimal("10.0000")
    # No additional costs: landed = invoice cost
    assert allocate_costs([(3, Decimal("1.5"))], Decimal("0"))[0].landed_unit_cost == Decimal("1.5000")


# ---------- Suppliers ----------

def test_suppliers(owner_client):
    s = supplier(owner_client)
    assert s["balance"] == "0.00" and s["phone"] == "0803 111 2222"
    assert owner_client.post("/api/suppliers", json={"name": "slot distribution ltd"}).status_code == 409
    assert owner_client.post("/api/suppliers", json={"name": "X Y", "email": "nope"}).status_code == 422

    r = owner_client.patch(f"/api/suppliers/{s['id']}", json={"address": "Lagos", "phone": ""})
    assert r.status_code == 200 and r.json()["address"] == "Lagos" and r.json()["phone"] is None
    assert owner_client.patch(f"/api/suppliers/{s['id']}", json={"name": None}).status_code == 422

    supplier(owner_client, "Oraimo Nigeria")
    owner_client.patch(f"/api/suppliers/{s['id']}", json={"is_active": False})
    assert [x["name"] for x in owner_client.get("/api/suppliers").json()] == ["Oraimo Nigeria"]
    assert len(owner_client.get("/api/suppliers", params={"include_inactive": True}).json()) == 2
    assert owner_client.get("/api/suppliers", params={"q": "oraimo"}).json()[0]["name"] == "Oraimo Nigeria"
    assert owner_client.get("/api/suppliers/999").status_code == 404

    actions = {e["action"] for e in owner_client.get("/api/audit-logs",
                                                     params={"action": "supplier"}).json()["items"]}
    assert actions == {"supplier.created", "supplier.updated"}


# ---------- Drafts ----------

def test_draft_changes_nothing_and_computes_landed_costs(owner_client):
    s = supplier(owner_client)
    charger = make_product(owner_client)
    a54 = phone(owner_client)
    p = draft(owner_client, s["id"], [
        {"product_id": charger["id"], "quantity": 10, "unit_cost": "4000"},
        {"product_id": a54["id"], "quantity": 2, "unit_cost": "380000",
         "units": [{"imei1": imei(1)}, {"imei1": imei(2), "serial_number": "r58x1", "condition": "used"}]},
    ], additional_costs="8000", supplier_invoice_no="INV-77")

    assert p["reference"] == f"PO-{TODAY.year}-00001" and p["status"] == "draft"
    assert p["subtotal"] == "800000.00" and p["total"] == "808000.00"
    assert [line["landed_unit_cost"] for line in p["lines"]] == ["4040.0000", "383800.0000"]
    assert p["lines"][1]["units"][1] == {"serial_number": "R58X1", "imei1": imei(2), "imei2": None,
                                         "condition": "used"}
    assert p["balance_due"] == "0.00"  # nothing owed on a draft

    # Nothing moved
    assert owner_client.get(f"/api/products/{charger['id']}").json()["qty_on_hand"] == 0
    assert owner_client.get("/api/units").json()["total"] == 0
    assert owner_client.get(f"/api/suppliers/{s['id']}").json()["balance"] == "0.00"


def test_draft_validation(owner_client):
    s = supplier(owner_client)
    charger = make_product(owner_client)
    a54 = phone(owner_client)
    other_phone = phone(owner_client, name="Registered phone")
    r = owner_client.post(f"/api/products/{other_phone['id']}/units/opening",
                          json={"units": [{"imei1": imei(9), "unit_cost": "1"}]})
    assert r.status_code == 201

    def attempt(lines, **extra):
        body = {"supplier_id": s["id"], "purchase_date": TODAY.isoformat(), "lines": lines, **extra}
        return owner_client.post("/api/purchases", json=body)

    r = attempt([{"product_id": a54["id"], "quantity": 2, "unit_cost": "1", "units": [{"imei1": imei(1)}]}])
    assert r.status_code == 422 and "2 unit(s) (1 entered)" in r.json()["detail"]
    r = attempt([{"product_id": charger["id"], "quantity": 1, "unit_cost": "1", "units": [{"imei1": imei(1)}]}])
    assert r.status_code == 422 and "counted by quantity" in r.json()["detail"]
    r = attempt([{"product_id": a54["id"], "quantity": 1, "unit_cost": "1", "units": [{"imei1": imei(9)}]}])
    assert r.status_code == 409 and r.json()["detail"].startswith("Line 1, unit 1: IMEI")
    r = attempt([{"product_id": a54["id"], "quantity": 1, "unit_cost": "1", "units": [{"imei1": imei(3)}]},
                 {"product_id": a54["id"], "quantity": 1, "unit_cost": "1",
                  "units": [{"imei1": imei(4), "imei2": imei(3)}]}])
    assert r.status_code == 409 and "entered twice" in r.json()["detail"]
    r = attempt([{"product_id": 999, "quantity": 1, "unit_cost": "1"}])
    assert r.status_code == 422
    r = attempt([], purchase_date=(TODAY + timedelta(days=1)).isoformat())
    assert r.status_code == 422 and "future" in r.json()["detail"]
    r = attempt([{"product_id": charger["id"], "quantity": 0, "unit_cost": "1"}])
    assert r.status_code == 422
    r = attempt([{"product_id": charger["id"], "quantity": 1, "unit_cost": "-1"}])
    assert r.status_code == 422

    owner_client.patch(f"/api/products/{charger['id']}", json={"is_active": False})
    r = attempt([{"product_id": charger["id"], "quantity": 1, "unit_cost": "1"}])
    assert r.status_code == 422 and "turned off" in r.json()["detail"]

    # Same supplier invoice twice (ignoring case) is refused; a different supplier may reuse it
    assert attempt([], supplier_invoice_no="INV-1").status_code == 201
    r = attempt([], supplier_invoice_no="inv-1")
    assert r.status_code == 409 and "already entered as PO-" in r.json()["detail"]
    other = supplier(owner_client, "Another supplier")
    r = owner_client.post("/api/purchases", json={"supplier_id": other["id"], "purchase_date": TODAY.isoformat(),
                                                  "supplier_invoice_no": "INV-1"})
    assert r.status_code == 201

    owner_client.patch(f"/api/suppliers/{other['id']}", json={"is_active": False})
    r = owner_client.post("/api/purchases", json={"supplier_id": other["id"], "purchase_date": TODAY.isoformat()})
    assert r.status_code == 422 and "turned off" in r.json()["detail"]


def test_draft_double_submit_creates_one_draft(owner_client):
    s = supplier(owner_client)
    body = {"supplier_id": s["id"], "purchase_date": TODAY.isoformat(), "idempotency_key": key()}
    first = owner_client.post("/api/purchases", json=body)
    again = owner_client.post("/api/purchases", json=body)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert owner_client.get("/api/purchases").json()["total"] == 1


def test_edit_and_discard_draft(owner_client):
    s = supplier(owner_client)
    charger = make_product(owner_client)
    cable = make_product(owner_client, name="Cable")
    p = draft(owner_client, s["id"], [{"product_id": charger["id"], "quantity": 5, "unit_cost": "4000"}])

    body = {"supplier_id": s["id"], "purchase_date": TODAY.isoformat(), "notes": "Second delivery",
            "lines": [{"product_id": cable["id"], "quantity": 20, "unit_cost": "800"},
                      {"product_id": charger["id"], "quantity": 2, "unit_cost": "4100"}]}
    r = owner_client.put(f"/api/purchases/{p['id']}", json=body)
    assert r.status_code == 200, r.text
    assert [line["product_name"] for line in r.json()["lines"]] == ["Cable", "Oraimo 20W charger"]
    assert r.json()["total"] == "24200.00" and r.json()["notes"] == "Second delivery"

    assert owner_client.delete(f"/api/purchases/{p['id']}").status_code == 204
    assert owner_client.get(f"/api/purchases/{p['id']}").status_code == 404
    log = owner_client.get("/api/audit-logs", params={"action": "purchase.deleted"}).json()["items"]
    assert log[0]["details"]["reference"] == p["reference"]


# ---------- Posting ----------

def test_posting_receives_stock_at_landed_cost(owner_client):
    s = supplier(owner_client)
    charger = make_product(owner_client)
    opening(owner_client, charger["id"], 10, "4000")  # 10 already on the shelf at 4,000
    a54 = phone(owner_client)
    p = draft(owner_client, s["id"], [
        {"product_id": charger["id"], "quantity": 10, "unit_cost": "4400"},
        {"product_id": a54["id"], "quantity": 2, "unit_cost": "378000",
         "units": [{"imei1": imei(1)}, {"imei1": imei(2), "imei2": imei(3)}]},
    ], additional_costs="8000")
    # Lines are worth 44,000 and 756,000 (800,000 in all), so ₦8,000 transport splits 440 / 7,560.
    # Landed: charger 4,400 + 440/10 = 4,444; phone 378,000 + 7,560/2 = 381,780
    assert [line["landed_unit_cost"] for line in p["lines"]] == ["4444.0000", "381780.0000"]

    posted = post(owner_client, p["id"])
    assert posted["status"] == "posted" and posted["posted_by_name"] == "Jalal Owner"
    assert posted["balance_due"] == "808000.00"

    # Weighted average: (10 × 4,000 + 10 × 4,444) / 20 = 4,222
    c = owner_client.get(f"/api/products/{charger['id']}").json()
    assert c["qty_on_hand"] == 20 and c["avg_cost"] == "4222.0000"

    units = owner_client.get("/api/units", params={"product_id": a54["id"]}).json()["items"]
    assert {u["unit_cost"] for u in units} == {"381780.0000"}
    assert {u["status"] for u in units} == {"in_stock"} and len(units) == 2
    assert owner_client.get(f"/api/products/{a54['id']}").json()["qty_on_hand"] == 2

    moves = owner_client.get("/api/stock-movements", params={"movement_type": "purchase"}).json()["items"]
    assert len(moves) == 3
    assert {(m["reference_type"], m["reference_id"]) for m in moves} == {("purchase", p["id"])}
    assert owner_client.get(f"/api/suppliers/{s['id']}").json()["balance"] == "808000.00"

    # Posting again (double click) changes nothing
    again = post(owner_client, p["id"])
    assert again["status"] == "posted"
    assert owner_client.get(f"/api/products/{charger['id']}").json()["qty_on_hand"] == 20
    log = owner_client.get("/api/audit-logs", params={"action": "purchase.posted"}).json()
    assert log["total"] == 1

    # Opening stock is now closed for the phone; posted purchases are frozen
    assert owner_client.get(f"/api/products/{a54['id']}").json()["opening_stock_open"] is False
    r = owner_client.post(f"/api/products/{a54['id']}/units/opening",
                          json={"units": [{"imei1": imei(4), "unit_cost": "1"}]})
    assert r.status_code == 409
    assert owner_client.put(f"/api/purchases/{p['id']}", json={
        "supplier_id": s["id"], "purchase_date": TODAY.isoformat()}).status_code == 409
    assert owner_client.delete(f"/api/purchases/{p['id']}").status_code == 409


def test_posting_rechecks_imeis_registered_since_the_draft(owner_client):
    s = supplier(owner_client)
    a54 = phone(owner_client)
    other = phone(owner_client, name="Other phone")
    p = draft(owner_client, s["id"], [{"product_id": a54["id"], "quantity": 1, "unit_cost": "1",
                                       "units": [{"imei1": imei(1)}]}])
    owner_client.post(f"/api/products/{other['id']}/units/opening",
                      json={"units": [{"imei1": imei(1), "unit_cost": "1"}]})
    r = owner_client.post(f"/api/purchases/{p['id']}/post")
    assert r.status_code == 409 and "already registered" in r.json()["detail"]
    assert owner_client.get(f"/api/purchases/{p['id']}").json()["status"] == "draft"
    assert owner_client.get(f"/api/products/{a54['id']}").json()["qty_on_hand"] == 0


def test_cannot_post_empty_or_with_turned_off_product(owner_client):
    s = supplier(owner_client)
    empty = draft(owner_client, s["id"], [])
    r = owner_client.post(f"/api/purchases/{empty['id']}/post")
    assert r.status_code == 422 and "at least one product" in r.json()["detail"]

    charger = make_product(owner_client)
    p = draft(owner_client, s["id"], [{"product_id": charger["id"], "quantity": 1, "unit_cost": "1"}])
    owner_client.patch(f"/api/products/{charger['id']}", json={"is_active": False})
    r = owner_client.post(f"/api/purchases/{p['id']}/post")
    assert r.status_code == 422 and "turned off" in r.json()["detail"]
    assert owner_client.post("/api/purchases/999/post").status_code == 404


@pytest.mark.parametrize("sql", [
    "UPDATE purchases SET total = 1, subtotal = 1, additional_costs = 0",
    "DELETE FROM purchases",
    "UPDATE purchase_lines SET unit_cost = 1",
    "DELETE FROM purchase_lines",
])
def test_database_freezes_posted_purchases(owner_client, sql):
    s = supplier(owner_client)
    charger = make_product(owner_client)
    p = draft(owner_client, s["id"], [{"product_id": charger["id"], "quantity": 2, "unit_cost": "100"}])
    post(owner_client, p["id"])
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text(sql))
    assert owner_client.get(f"/api/purchases/{p['id']}").json()["total"] == "200.00"


# ---------- Payments ----------

def _posted(owner_client, total_each="100000", n=1, name="Slot Distribution Ltd"):
    s = supplier(owner_client, name)
    charger = make_product(owner_client)
    purchases = [post(owner_client, draft(owner_client, s["id"], [
        {"product_id": charger["id"], "quantity": 1, "unit_cost": total_each}])["id"]) for _ in range(n)]
    return s, purchases


def test_payments_reduce_balance_and_never_overpay(owner_client):
    s, (p1, p2) = _posted(owner_client, n=2)  # owes 200,000

    r = pay(owner_client, s["id"], "30000", purchase_id=p1["id"], reference="TRF-881")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["purchase_reference"] == p1["reference"] and body["method_name"] == "Cash"
    assert body["paid_on"] == TODAY.isoformat() and body["recorded_by_name"] == "Jalal Owner"
    assert owner_client.get(f"/api/purchases/{p1['id']}").json()["balance_due"] == "70000.00"

    # More than is left on that purchase
    r = pay(owner_client, s["id"], "70000.01", purchase_id=p1["id"])
    assert r.status_code == 409 and "70,000.00" in r.json()["detail"]
    # On account: up to the whole remaining balance (170,000)
    assert pay(owner_client, s["id"], "170000.01").status_code == 409
    assert pay(owner_client, s["id"], "100000").status_code == 201
    sup = owner_client.get(f"/api/suppliers/{s['id']}").json()
    assert sup["paid_total"] == "130000.00" and sup["balance"] == "70000.00"
    assert pay(owner_client, s["id"], "70000").status_code == 201
    assert pay(owner_client, s["id"], "0.01").status_code == 409  # nothing owed now

    history = owner_client.get("/api/supplier-payments", params={"supplier_id": s["id"]}).json()
    assert history["total"] == 3
    assert owner_client.get("/api/purchases", params={"unpaid_only": True}).json()["total"] == 2
    log = owner_client.get("/api/audit-logs", params={"action": "supplier_payment"}).json()
    assert log["total"] == 3


def test_payment_validation(owner_client):
    s, (p1,) = _posted(owner_client)
    other, (p_other,) = _posted(owner_client, name="Other supplier")
    d = draft(owner_client, s["id"], [])

    assert pay(owner_client, s["id"], "0").status_code == 422
    assert pay(owner_client, s["id"], "-5").status_code == 422
    assert pay(owner_client, s["id"], "1.001").status_code == 422
    assert pay(owner_client, s["id"], "1", purchase_id=p_other["id"]).status_code == 422
    r = pay(owner_client, s["id"], "1", purchase_id=d["id"])
    assert r.status_code == 422 and "not posted" in r.json()["detail"]
    assert pay(owner_client, s["id"], "1", paid_on=(TODAY + timedelta(days=1)).isoformat()).status_code == 422
    assert pay(owner_client, 999, "1").status_code == 422
    owner_client.patch("/api/payment-methods/1", json={"is_active": False})
    r = pay(owner_client, s["id"], "1")
    assert r.status_code == 422 and "payment method" in r.json()["detail"]
    assert other["id"] != s["id"] and p1["id"]


def test_payment_double_submit_is_recorded_once(owner_client):
    s, _ = _posted(owner_client)
    k = key()
    first = pay(owner_client, s["id"], "40000", idempotency_key=k)
    again = pay(owner_client, s["id"], "40000", idempotency_key=k)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert owner_client.get(f"/api/suppliers/{s['id']}").json()["paid_total"] == "40000.00"
    # Re-using a form for a different amount is refused, not silently ignored
    r = pay(owner_client, s["id"], "1000", idempotency_key=k)
    assert r.status_code == 409 and "already used" in r.json()["detail"]


def test_two_simultaneous_payments_cannot_overpay(owner, owner_client):
    s, _ = _posted(owner_client)  # owes 100,000
    clients = [logged_in("owner"), logged_in("owner")]
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def submit(c):
        barrier.wait()
        codes.append(pay(c, s["id"], "100000").status_code)

    threads = [threading.Thread(target=submit, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201, 409]
    assert owner_client.get(f"/api/suppliers/{s['id']}").json()["balance"] == "0.00"


def test_supplier_payments_are_append_only(owner_client):
    s, _ = _posted(owner_client)
    pay(owner_client, s["id"], "500")
    for sql in ("UPDATE supplier_payments SET amount = 1", "DELETE FROM supplier_payments"):
        with pytest.raises(DBAPIError, match="cannot be changed or deleted"):
            with engine.begin() as conn:
                conn.execute(text(sql))


def test_purchase_list_filters(owner_client):
    s, (p1,) = _posted(owner_client)
    d = draft(owner_client, s["id"], [], supplier_invoice_no="ABC-123")

    def refs(**params):
        return [p["reference"] for p in owner_client.get("/api/purchases", params=params).json()["items"]]

    assert set(refs()) == {p1["reference"], d["reference"]}
    assert refs(status="draft") == [d["reference"]]
    assert refs(q="abc-1") == [d["reference"]]
    assert refs(q=p1["reference"]) == [p1["reference"]]
    assert refs(supplier_id=999) == []
    assert owner_client.get("/api/purchases", params={"status": "paid"}).status_code == 422
