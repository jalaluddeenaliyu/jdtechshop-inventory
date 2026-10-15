"""Staff must not be able to do owner-only things, even by calling the API directly."""
import pytest

from tests.conftest import PASSWORD, logged_in, make_user, new_client

OWNER_ONLY = [
    ("get", "/api/users", None),
    ("post", "/api/users", {"full_name": "X Y", "username": "xy1", "password": "Password123",
                            "role": "owner"}),
    ("patch", "/api/users/1", {"role": "staff"}),
    ("post", "/api/users/1/reset-password", {"new_password": "Hijacked123"}),
    ("put", "/api/settings", {"business_name": "Hacked", "default_reorder_level": 1,
                              "staff_max_discount_percent": "100",
                              "allow_staff_credit_sales": True}),
    ("post", "/api/payment-methods", {"name": "Crypto"}),
    ("patch", "/api/payment-methods/1", {"is_active": False}),
    ("get", "/api/audit-logs", None),
    ("post", "/api/categories", {"name": "Phones"}),
    ("patch", "/api/categories/1", {"name": "Hacked"}),
    ("post", "/api/brands", {"name": "Tecno"}),
    ("patch", "/api/brands/1", {"name": "Hacked"}),
    ("post", "/api/products", {"name": "Free phone", "tracking_type": "quantity",
                               "selling_price": "0"}),
    ("patch", "/api/products/1", {"selling_price": "1"}),
    ("post", "/api/products/1/opening-stock", {"quantity": 100, "unit_cost": "1"}),
    ("get", "/api/products/1/movements", None),
    ("get", "/api/stock-movements", None),
    ("post", "/api/products/1/units/opening", {"units": [{"serial_number": "SN123",
                                                          "unit_cost": "1"}]}),
    ("patch", "/api/units/1", {"notes": "Hacked"}),
    ("get", "/api/suppliers", None),
    ("get", "/api/suppliers/1", None),
    ("post", "/api/suppliers", {"name": "Fake supplier"}),
    ("patch", "/api/suppliers/1", {"name": "Hacked"}),
    ("get", "/api/purchases", None),
    ("get", "/api/purchases/1", None),
    ("post", "/api/purchases", {"supplier_id": 1, "purchase_date": "2026-01-01"}),
    ("put", "/api/purchases/1", {"supplier_id": 1, "purchase_date": "2026-01-01"}),
    ("delete", "/api/purchases/1", None),
    ("post", "/api/purchases/1/post", None),
    ("get", "/api/supplier-payments", None),
    ("post", "/api/supplier-payments", {"supplier_id": 1, "amount": "1", "method_id": 1,
                                        "idempotency_key": "abcdefgh12"}),
    ("post", "/api/sales/1/returns", {"lines": [{"sale_line_id": 1}], "reason": "Changed mind",
                                      "idempotency_key": "abcdefgh12"}),
    ("post", "/api/sales/1/reverse", {"reason": "Mistake"}),
    ("post", "/api/purchases/1/returns", {"lines": [{"purchase_line_id": 1}], "reason": "Faulty",
                                          "idempotency_key": "abcdefgh12"}),
    ("post", "/api/purchases/1/reverse", {"reason": "Mistake"}),
    ("post", "/api/customer-payments/1/reverse", {"reason": "Mistake"}),
    ("post", "/api/supplier-payments/1/reverse", {"reason": "Mistake"}),
    ("post", "/api/stock-adjustments/1/approve", {}),
    ("post", "/api/stock-adjustments/1/reject", {}),
]


@pytest.mark.parametrize("method,url,body", OWNER_ONLY)
def test_staff_gets_403_on_owner_endpoints(owner, staff_client, method, url, body):
    r = getattr(staff_client, method)(url, **({"json": body} if body is not None else {}))
    assert r.status_code == 403, (url, r.text)


@pytest.mark.parametrize("method,url,body", OWNER_ONLY)
def test_anonymous_gets_401_on_owner_endpoints(owner, method, url, body):
    r = getattr(new_client(), method)(url, **({"json": body} if body is not None else {}))
    assert r.status_code == 401


def test_staff_can_read_settings_and_payment_methods(staff_client):
    assert staff_client.get("/api/settings").status_code == 200
    names = [p["name"] for p in staff_client.get("/api/payment-methods").json()]
    assert names == ["Cash", "Bank transfer", "POS terminal"]


def test_owner_manages_users_end_to_end(owner_client):
    r = owner_client.post("/api/users", json={"full_name": "  Musa   Bello ",
                                               "username": "Musa.B", "password": "Welcome123"})
    assert r.status_code == 201, r.text
    musa = r.json()
    assert musa["username"] == "musa.b" and musa["full_name"] == "Musa Bello"
    assert musa["role"] == "staff"

    # duplicate username (case-insensitive)
    r = owner_client.post("/api/users", json={"full_name": "Other", "username": "MUSA.B",
                                               "password": "Welcome123"})
    assert r.status_code == 409

    musa_client = logged_in("musa.b", "Welcome123")
    # Owner deactivates Musa -> his existing session stops working immediately
    r = owner_client.patch(f"/api/users/{musa['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert musa_client.get("/api/auth/me").status_code == 401
    r = new_client().post("/api/auth/login", json={"username": "musa.b", "password": "Welcome123"})
    assert r.status_code == 401

    # Reactivate and reset password
    owner_client.patch(f"/api/users/{musa['id']}", json={"is_active": True})
    r = owner_client.post(f"/api/users/{musa['id']}/reset-password",
                          json={"new_password": "Fresh-pass9"})
    assert r.status_code == 200
    assert new_client().post("/api/auth/login", json={"username": "musa.b",
                             "password": "Fresh-pass9"}).status_code == 200

    actions = [e["action"] for e in owner_client.get("/api/audit-logs").json()["items"]]
    assert {"user.created", "user.updated", "user.password_reset"} <= set(actions)


def test_role_change_takes_effect_immediately(owner_client, staff):
    staff_c = logged_in("cashier")
    assert staff_c.get("/api/users").status_code == 403
    owner_client.patch(f"/api/users/{staff.id}", json={"role": "owner"})
    assert staff_c.get("/api/users").status_code == 401  # old session invalidated
    assert logged_in("cashier").get("/api/users").status_code == 200


def test_cannot_remove_last_owner_or_demote_self(owner, owner_client):
    r = owner_client.patch(f"/api/users/{owner.id}", json={"role": "staff"})
    assert r.status_code == 409
    r = owner_client.patch(f"/api/users/{owner.id}", json={"is_active": False})
    assert r.status_code == 409

    second = make_user("partner", "owner")
    r = owner_client.patch(f"/api/users/{second.id}", json={"is_active": False})
    assert r.status_code == 200  # fine: original owner still active


def test_user_validation_messages(owner_client):
    r = owner_client.post("/api/users", json={"full_name": "A", "username": "ok_name",
                                               "password": "Welcome123"})
    assert r.status_code == 422 and "Full name" in r.json()["detail"]
    r = owner_client.post("/api/users", json={"full_name": "Ab Cd", "username": "bad name!",
                                               "password": "Welcome123"})
    assert r.status_code == 422 and "Username" in r.json()["detail"]
    r = owner_client.post("/api/users", json={"full_name": "Ab Cd", "username": "good",
                                               "password": "Welcome123", "role": "admin"})
    assert r.status_code == 422


def test_unknown_user_returns_404(owner_client):
    assert owner_client.patch("/api/users/9999", json={"full_name": "Nobody"}).status_code == 404


def test_password_never_returned(owner_client):
    for u in owner_client.get("/api/users").json():
        assert "password_hash" not in u and PASSWORD not in str(u)
