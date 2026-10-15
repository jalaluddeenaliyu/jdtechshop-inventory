"""Serialized units: serial / IMEI 1 / IMEI 2, uniqueness across slots, opening stock unit by unit."""
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database import engine
from app.schemas.units import luhn_ok
from tests.conftest import logged_in
from tests.test_products import make_product, opening


def imei(n: int) -> str:
    """A valid 15-digit IMEI (correct check digit) built from a number."""
    body = f"35{n:012d}"
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError


def phone(client, **overrides) -> dict:
    return make_product(client, **{"name": "Galaxy A54 128GB", "tracking_type": "serialized",
                                   "selling_price": "420000", **overrides})


def add_units(client, product_id: int, units: list[dict], note: str | None = None):
    return client.post(f"/api/products/{product_id}/units/opening",
                       json={"units": units, **({"note": note} if note else {})})


def test_luhn_and_imei_helper():
    assert luhn_ok("490154203237518")  # a well-known valid example
    assert not luhn_ok("490154203237519")
    assert len(imei(1)) == 15 and luhn_ok(imei(1))


def test_opening_units_create_stock_and_ledger(owner_client):
    p = phone(owner_client)
    r = add_units(owner_client, p["id"], [
        {"imei1": imei(1), "imei2": imei(2), "unit_cost": "380000", "condition": "new"},
        {"imei1": "35 0000 0000 0003-" + imei(3)[-1], "unit_cost": "350000.5",
         "condition": "used", "notes": "Small scratch"},
        {"serial_number": " r58n12abcde ", "unit_cost": "300000"},
    ], note="Shelf count")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["product"]["qty_on_hand"] == 3
    assert body["product"]["opening_stock_open"] is True  # more opening batches still allowed
    # Average across the three units: (380000 + 350000.5 + 300000) / 3
    assert body["product"]["avg_cost"] == "343333.5000"
    units = body["units"]
    assert units[1]["imei1"] == imei(3)  # spaces and dashes removed
    assert units[2]["serial_number"] == "R58N12ABCDE" and units[2]["condition"] == "new"
    assert {u["status"] for u in units} == {"in_stock"}

    moves = owner_client.get(f"/api/products/{p['id']}/movements").json()["items"]
    assert [m["quantity_change"] for m in moves] == [1, 1, 1]
    assert [m["balance_after"] for m in moves] == [3, 2, 1]  # newest first
    assert moves[0]["unit_label"] == "R58N12ABCDE" and moves[2]["unit_label"] == imei(1)
    assert moves[1]["unit_cost"] == "350000.5000" and moves[0]["reason"] == "Shelf count"

    log = owner_client.get("/api/audit-logs", params={"action": "stock.opening_units"}).json()["items"][0]
    assert log["details"]["count"] == 3 and log["details"]["total_cost"] == "1030000.5000"


def test_more_opening_batches_allowed_until_other_movements(owner, owner_client):
    p = phone(owner_client)
    assert add_units(owner_client, p["id"], [{"imei1": imei(1), "unit_cost": "1"}]).status_code == 201
    r = add_units(owner_client, p["id"], [{"imei1": imei(2), "unit_cost": "1"}])
    assert r.status_code == 201 and r.json()["product"]["qty_on_hand"] == 2

    # Once anything other than opening stock happens, opening is closed.
    with engine.begin() as conn:
        unit_id = conn.execute(text("SELECT id FROM product_units ORDER BY id LIMIT 1")).scalar()
        conn.execute(text("UPDATE product_units SET status = 'damaged' WHERE id = :u"), {"u": unit_id})
        conn.execute(text("UPDATE products SET qty_on_hand = 1 WHERE id = :p"), {"p": p["id"]})
        conn.execute(text(
            "INSERT INTO stock_movements (product_id, unit_id, quantity_change, balance_after, "
            "movement_type) VALUES (:p, :u, -1, 1, 'damage')"), {"p": p["id"], "u": unit_id})
    r = add_units(owner_client, p["id"], [{"imei1": imei(3), "unit_cost": "1"}])
    assert r.status_code == 409 and "opening stock is closed" in r.json()["detail"]


@pytest.mark.parametrize("units,message", [
    ([{"unit_cost": "1"}], "serial number or an IMEI"),
    ([{"imei2": imei(1), "unit_cost": "1"}], "IMEI 1 before IMEI 2"),
    ([{"imei1": imei(1), "imei2": imei(1), "unit_cost": "1"}], "must be different"),
    ([{"imei1": "12345", "unit_cost": "1"}], "exactly 15 digits"),
    ([{"imei1": imei(1)[:-1] + str((int(imei(1)[-1]) + 1) % 10), "unit_cost": "1"}], "mistyped digit"),
    ([{"serial_number": "AB", "unit_cost": "1"}], "Serial number"),
    ([{"imei1": imei(1), "unit_cost": "-5"}], None),
    ([{"imei1": imei(1)}], None),  # cost is required
    ([], None),
])
def test_unit_validation(owner_client, units, message):
    p = phone(owner_client)
    r = add_units(owner_client, p["id"], units)
    assert r.status_code == 422, r.text
    if message:
        assert message in r.json()["detail"]
    assert owner_client.get(f"/api/products/{p['id']}").json()["qty_on_hand"] == 0


def test_imei_unique_across_units_and_slots(owner_client):
    a54 = phone(owner_client)
    iphone = phone(owner_client, name="iPhone 13")
    assert add_units(owner_client, a54["id"], [
        {"imei1": imei(1), "imei2": imei(2), "serial_number": "SN-1", "unit_cost": "1"},
    ]).status_code == 201

    cases = [
        ({"imei1": imei(1)}, "IMEI " + imei(1)),            # same slot
        ({"imei1": imei(2)}, "IMEI " + imei(2)),            # A's IMEI 2 used as IMEI 1
        ({"imei1": imei(5), "imei2": imei(1)}, imei(1)),    # A's IMEI 1 used as IMEI 2
        ({"serial_number": "sn-1"}, "serial number SN-1"),  # serials ignore case
    ]
    for unit, needle in cases:
        r = add_units(owner_client, iphone["id"], [
            {"imei1": imei(9), "unit_cost": "1"},  # a good row first: the batch is all-or-nothing
            {**unit, "unit_cost": "1"},
        ])
        assert r.status_code == 409, unit
        detail = r.json()["detail"]
        assert detail.startswith("Row 2:") and needle in detail and "Galaxy A54 128GB" in detail
    assert owner_client.get(f"/api/products/{iphone['id']}").json()["qty_on_hand"] == 0

    # Duplicates inside one batch, including across slots
    r = add_units(owner_client, iphone["id"], [
        {"imei1": imei(7), "unit_cost": "1"},
        {"imei1": imei(8), "imei2": imei(7), "unit_cost": "1"},
    ])
    assert r.status_code == 409 and "entered twice" in r.json()["detail"]


def test_database_itself_refuses_duplicate_imeis_across_slots(owner_client):
    """Even code that skips the checks cannot save a duplicate: the registry's key refuses it."""
    p = phone(owner_client)
    add_units(owner_client, p["id"], [{"imei1": imei(1), "imei2": imei(2), "unit_cost": "1"}])
    with pytest.raises(IntegrityError, match="imei_registry"):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO product_units (product_id, imei1, condition, unit_cost, status) "
                "VALUES (:p, :i, 'new', 1, 'in_stock')"), {"p": p["id"], "i": imei(2)})


def test_database_keeps_units_and_quantity_in_step(owner_client):
    p = phone(owner_client)
    add_units(owner_client, p["id"], [{"imei1": imei(1), "unit_cost": "1"}])
    # A unit leaving stock without the matching ledger movement is refused at commit
    with pytest.raises(IntegrityError, match="units on hand"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE product_units SET status = 'sold'"))
    # Units cannot be deleted or moved to another product
    other = phone(owner_client, name="Other phone")
    for sql in ("DELETE FROM product_units",
                f"UPDATE product_units SET product_id = {other['id']}"):
        with pytest.raises(DBAPIError):
            with engine.begin() as conn:
                conn.execute(text(sql))
    # A quantity product cannot own units
    charger = make_product(owner_client)
    with pytest.raises(IntegrityError, match="counted by quantity"):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO product_units (product_id, serial_number, condition, unit_cost, status) "
                "VALUES (:p, 'ZZZ1', 'new', 1, 'sold')"), {"p": charger["id"]})


def test_wrong_product_types_and_states(owner_client):
    charger = make_product(owner_client)
    r = add_units(owner_client, charger["id"], [{"imei1": imei(1), "unit_cost": "1"}])
    assert r.status_code == 409 and "counted by quantity" in r.json()["detail"]

    p = phone(owner_client)
    owner_client.patch(f"/api/products/{p['id']}", json={"is_active": False})
    r = add_units(owner_client, p["id"], [{"imei1": imei(1), "unit_cost": "1"}])
    assert r.status_code == 409 and "turned off" in r.json()["detail"]
    assert add_units(owner_client, 999, [{"imei1": imei(1), "unit_cost": "1"}]).status_code == 404

    # A product with units cannot switch to quantity counting
    p2 = phone(owner_client, name="Tecno Spark 20")
    add_units(owner_client, p2["id"], [{"imei1": imei(2), "unit_cost": "1"}])
    r = owner_client.patch(f"/api/products/{p2['id']}", json={"tracking_type": "quantity"})
    assert r.status_code == 409


def test_correcting_a_unit_is_checked_and_audited(owner_client, staff_client):
    p = phone(owner_client)
    units = add_units(owner_client, p["id"], [
        {"imei1": imei(1), "unit_cost": "1000"},
        {"imei1": imei(2), "imei2": imei(3), "unit_cost": "1000"},
    ]).json()["units"]
    first, second = units[0]["id"], units[1]["id"]

    r = owner_client.patch(f"/api/units/{first}", json={"imei2": imei(4), "condition": "refurbished",
                                                        "notes": "Box opened"})
    assert r.status_code == 200, r.text
    assert r.json()["imei2"] == imei(4) and r.json()["condition"] == "refurbished"

    # Taking another unit's IMEI (either slot) is refused; the registry follows corrections
    r = owner_client.patch(f"/api/units/{first}", json={"imei2": imei(3)})
    assert r.status_code == 409 and f"unit #{second}" in r.json()["detail"]
    owner_client.patch(f"/api/units/{second}", json={"imei2": None})
    assert owner_client.patch(f"/api/units/{first}", json={"imei2": imei(3)}).status_code == 200

    # Removing the only identifier is refused; cost and status cannot be edited here
    assert owner_client.patch(f"/api/units/{second}", json={"imei1": None}).status_code == 422
    assert owner_client.patch(f"/api/units/{second}", json={"unit_cost": "1"}).status_code == 422
    assert owner_client.patch(f"/api/units/{second}", json={"status": "sold"}).status_code == 422
    assert owner_client.patch(f"/api/units/{second}", json={"condition": None}).status_code == 422
    assert owner_client.patch("/api/units/999", json={"notes": "x"}).status_code == 404
    assert staff_client.patch(f"/api/units/{second}", json={"notes": "x"}).status_code == 403

    log = owner_client.get("/api/audit-logs", params={"action": "unit.updated"}).json()["items"]
    assert log[-1]["details"]["imei2"] == {"from": None, "to": imei(4)}


def test_unit_lookup_and_product_search_by_imei(owner_client, staff_client):
    a54 = phone(owner_client)
    iphone = phone(owner_client, name="iPhone 13")
    add_units(owner_client, a54["id"], [{"imei1": imei(1), "serial_number": "R58N1", "unit_cost": "380000"}])
    add_units(owner_client, iphone["id"], [{"imei1": imei(2), "imei2": imei(3), "unit_cost": "500000"}])

    found = owner_client.get("/api/units", params={"q": imei(3)[-6:]}).json()
    assert found["total"] == 1 and found["items"][0]["product_name"] == "iPhone 13"
    assert found["items"][0]["unit_cost"] == "500000.0000"

    seen = staff_client.get("/api/units", params={"q": "r58n"}).json()
    assert seen["items"][0]["product_name"] == "Galaxy A54 128GB"
    assert seen["items"][0]["unit_cost"] is None  # staff never see unit costs
    unit_id = seen["items"][0]["id"]
    assert staff_client.get(f"/api/units/{unit_id}").json()["unit_cost"] is None
    assert staff_client.get("/api/units/999").status_code == 404

    assert owner_client.get("/api/units", params={"product_id": a54["id"]}).json()["total"] == 1
    assert owner_client.get("/api/units", params={"status": "sold"}).json()["total"] == 0
    assert owner_client.get("/api/units", params={"status": "lost"}).status_code == 422

    names = [p["name"] for p in staff_client.get("/api/products", params={"q": imei(2)}).json()["items"]]
    assert names == ["iPhone 13"]


def test_two_owners_registering_the_same_imei_at_once(owner, owner_client):
    """Whoever is second gets a clear refusal, never a duplicate."""
    a = phone(owner_client, name="Phone A")
    b = phone(owner_client, name="Phone B")
    clients = [logged_in("owner"), logged_in("owner")]
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def submit(c, product_id):
        barrier.wait()
        codes.append(add_units(c, product_id, [{"imei1": imei(1), "unit_cost": "1"}]).status_code)

    threads = [threading.Thread(target=submit, args=(c, pid))
               for c, pid in zip(clients, (a["id"], b["id"]), strict=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(codes) == [201, 409]
    assert owner_client.get("/api/units").json()["total"] == 1
    qty = [owner_client.get(f"/api/products/{p['id']}").json()["qty_on_hand"] for p in (a, b)]
    assert sorted(qty) == [0, 1]


def test_quantity_opening_stock_still_works_alongside(owner_client):
    charger = make_product(owner_client)
    assert opening(owner_client, charger["id"], 5)["qty_on_hand"] == 5
