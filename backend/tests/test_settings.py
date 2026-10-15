def _valid_settings(**overrides):
    body = {
        "business_name": "JD TechShop",
        "slogan": "Delivering Happiness",
        "address": "Sokoto, Nigeria",
        "phone": "0803 000 0000",
        "email": "",
        "receipt_footer": "Thank you",
        "default_reorder_level": 4,
        "staff_max_discount_percent": "7.5",
        "allow_staff_credit_sales": False,
    }
    body.update(overrides)
    return body


def test_owner_updates_settings_and_change_is_audited(owner_client):
    r = owner_client.put("/api/settings", json=_valid_settings())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["phone"] == "0803 000 0000"
    assert data["email"] is None  # blank becomes empty, not ""
    assert data["staff_max_discount_percent"] == "7.50"
    assert data["currency_code"] == "NGN"

    logs = owner_client.get("/api/audit-logs", params={"action": "settings"}).json()["items"]
    assert logs and logs[0]["details"]["default_reorder_level"] == {"from": "3", "to": "4"}
    assert logs[0]["user_name"] == "Jalal Owner"


def test_settings_validation(owner_client):
    r = owner_client.put("/api/settings", json=_valid_settings(staff_max_discount_percent="150"))
    assert r.status_code == 422
    r = owner_client.put("/api/settings", json=_valid_settings(email="not-an-email"))
    assert r.status_code == 422
    r = owner_client.put("/api/settings", json=_valid_settings(default_reorder_level=-1))
    assert r.status_code == 422


def test_payment_methods_add_rename_deactivate(owner_client, staff):
    r = owner_client.post("/api/payment-methods", json={"name": " Opay  wallet ", "sort_order": 4})
    assert r.status_code == 201 and r.json()["name"] == "Opay wallet"
    pm_id = r.json()["id"]

    # duplicate, case-insensitive
    assert owner_client.post("/api/payment-methods", json={"name": "CASH"}).status_code == 409
    assert owner_client.patch(f"/api/payment-methods/{pm_id}",
                              json={"name": "cash"}).status_code == 409

    r = owner_client.patch(f"/api/payment-methods/{pm_id}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    active = [p["name"] for p in owner_client.get("/api/payment-methods").json()]
    assert "Opay wallet" not in active
    everything = owner_client.get("/api/payment-methods",
                                  params={"include_inactive": True}).json()
    assert any(p["name"] == "Opay wallet" for p in everything)


def test_last_active_payment_method_cannot_be_disabled(owner_client):
    assert owner_client.patch("/api/payment-methods/2", json={"is_active": False}).status_code == 200
    assert owner_client.patch("/api/payment-methods/3", json={"is_active": False}).status_code == 200
    r = owner_client.patch("/api/payment-methods/1", json={"is_active": False})
    assert r.status_code == 409


def test_audit_log_pagination(owner_client):
    for i in range(3):
        owner_client.post("/api/payment-methods", json={"name": f"Method {i}"})
    page = owner_client.get("/api/audit-logs", params={"page_size": 2}).json()
    assert page["total"] >= 4 and len(page["items"]) == 2
    assert page["items"][0]["id"] > page["items"][1]["id"]  # newest first
