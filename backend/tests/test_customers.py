"""Customers and the built-in walk-in customer."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database import engine
from app.schemas.customers import normalize_phone


def add(client, name="Aisha Bello", **extra) -> dict:
    r = client.post("/api/customers", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize("raw,stored", [
    ("0803 123 4567", "08031234567"),
    ("+234 803 123 4567", "08031234567"),
    ("2348031234567", "08031234567"),
    ("803-123-4567", "08031234567"),
    ("(0803) 123.4567", "08031234567"),
    ("+44 7700 900123", "+447700900123"),
    ("   ", None),
])
def test_phone_numbers_are_stored_one_way(raw, stored):
    assert normalize_phone(raw) == stored


@pytest.mark.parametrize("bad", ["12345", "0803123456", "080312345678", "phone", "+12"])
def test_bad_phone_numbers_are_refused(bad):
    with pytest.raises(ValueError, match="like 0803 123 4567"):
        normalize_phone(bad)


def test_walk_in_customer_is_built_in_and_protected(owner_client, staff_client):
    walk_in = staff_client.get("/api/customers/walk-in").json()
    assert walk_in["name"] == "Walk-in customer" and walk_in["is_walk_in"] is True

    # Hidden from the normal list, shown first when asked for
    assert staff_client.get("/api/customers").json()["total"] == 0
    add(staff_client, "Aaron Musa")
    listed = staff_client.get("/api/customers", params={"include_walk_in": True}).json()["items"]
    assert [c["name"] for c in listed] == ["Walk-in customer", "Aaron Musa"]

    for body in ({"name": "Someone"}, {"is_active": False}, {"phone": "08031234567"}):
        r = owner_client.patch(f"/api/customers/{walk_in['id']}", json=body)
        assert r.status_code == 409 and "built in" in r.json()["detail"]

    # The database itself protects it too
    with pytest.raises(DBAPIError, match="walk-in customer cannot be changed"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE customers SET name = 'X' WHERE is_walk_in"))
    with pytest.raises(DBAPIError, match="Only the built-in walk-in"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE customers SET is_walk_in = true WHERE NOT is_walk_in"))
    with pytest.raises(DBAPIError):  # a second walk-in row
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO customers (name, is_walk_in, is_active) VALUES ('W2', true, true)"))


def test_customers_are_never_deleted(owner_client):
    add(owner_client)
    with pytest.raises(DBAPIError, match="cannot be deleted"):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM customers"))


def test_staff_add_and_edit_customers_with_audit(staff_client, owner_client):
    c = add(staff_client, "  Aisha   Bello ", phone="+234 803 123 4567", email="", address="Sokoto")
    assert c["name"] == "Aisha Bello" and c["phone"] == "08031234567"
    assert c["email"] is None and c["is_walk_in"] is False and c["is_active"] is True

    r = staff_client.patch(f"/api/customers/{c['id']}", json={"email": "aisha@example.com", "phone": ""})
    assert r.status_code == 200 and r.json()["email"] == "aisha@example.com" and r.json()["phone"] is None

    logs = owner_client.get("/api/audit-logs", params={"action": "customer"}).json()["items"]
    assert {e["action"] for e in logs} == {"customer.created", "customer.updated"}
    assert logs[0]["user_name"] == "Amina Cashier"


def test_only_owner_turns_customers_off(staff_client, owner_client):
    c = add(staff_client)
    r = staff_client.patch(f"/api/customers/{c['id']}", json={"is_active": False})
    assert r.status_code == 403 and "Only the owner" in r.json()["detail"]
    r = owner_client.patch(f"/api/customers/{c['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert staff_client.get("/api/customers").json()["total"] == 0
    assert staff_client.get("/api/customers", params={"include_inactive": True}).json()["total"] == 1
    # Sending the same value again is not a change, so staff may resend a full form
    assert staff_client.patch(f"/api/customers/{c['id']}",
                              json={"is_active": False, "notes": "VIP"}).status_code == 200


def test_one_phone_number_one_customer(staff_client):
    first = add(staff_client, phone="0803 123 4567")
    r = staff_client.post("/api/customers", json={"name": "Someone Else", "phone": "+2348031234567"})
    assert r.json()["detail"] == "The phone number 0803 123 4567 already belongs to Aisha Bello."
    other = add(staff_client, "Musa Ali", phone="08099998888")
    r = staff_client.patch(f"/api/customers/{other['id']}", json={"phone": "803 123 4567"})
    assert r.status_code == 409
    # Same name is fine: many people share names
    assert add(staff_client, "Aisha Bello")["id"] != first["id"]


def test_customer_validation(staff_client):
    assert staff_client.post("/api/customers", json={"name": "A"}).status_code == 422
    r = staff_client.post("/api/customers", json={"name": "Ok Name", "phone": "12345"})
    assert r.status_code == 422 and "0803 123 4567" in r.json()["detail"]
    assert staff_client.post("/api/customers", json={"name": "Ok Name", "email": "nope"}).status_code == 422
    assert staff_client.post("/api/customers", json={"name": "Ok Name", "is_walk_in": True}).status_code == 422
    c = add(staff_client)
    assert staff_client.patch(f"/api/customers/{c['id']}", json={"name": None}).status_code == 422
    assert staff_client.patch("/api/customers/999", json={"notes": "x"}).status_code == 404
    assert staff_client.get("/api/customers/999").status_code == 404


def test_search_by_name_email_or_phone_in_any_format(staff_client):
    add(staff_client, "Aisha Bello", phone="08031234567")
    add(staff_client, "Musa Ali", phone="07099990000", email="musa@shop.ng")
    add(staff_client, "Grace O'Neil", phone="+447700900123")

    def names(q):
        return [c["name"] for c in staff_client.get("/api/customers", params={"q": q}).json()["items"]]

    assert names("aisha") == ["Aisha Bello"]
    assert names("SHOP.NG") == ["Musa Ali"]
    assert names("0803 123") == ["Aisha Bello"]
    assert names("+234 803 123 4567") == ["Aisha Bello"]
    assert names("4567") == ["Aisha Bello"]
    assert names("7700 900") == ["Grace O'Neil"]
    assert names("%") == []
    page = staff_client.get("/api/customers", params={"page_size": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2


def test_customers_require_login(client):
    assert client.get("/api/customers").status_code == 401
    assert client.post("/api/customers", json={"name": "Nobody"}).status_code == 401
