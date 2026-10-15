"""Categories and brands."""


def test_categories_with_one_level_of_subcategories(owner_client, staff_client):
    r = owner_client.post("/api/categories", json={"name": "  Accessories "})
    assert r.status_code == 201, r.text
    accessories = r.json()
    assert accessories["name"] == "Accessories" and accessories["parent_id"] is None

    r = owner_client.post("/api/categories", json={"name": "Chargers",
                                                    "parent_id": accessories["id"]})
    assert r.status_code == 201
    chargers = r.json()
    assert chargers["parent_name"] == "Accessories"

    # Names are unique, ignoring case
    assert owner_client.post("/api/categories", json={"name": "CHARGERS"}).status_code == 409

    # Only one level deep
    r = owner_client.post("/api/categories", json={"name": "Fast chargers",
                                                    "parent_id": chargers["id"]})
    assert r.status_code == 422 and "one level" in r.json()["detail"]

    # A category with subcategories cannot itself become a subcategory
    phones = owner_client.post("/api/categories", json={"name": "Phones"}).json()
    r = owner_client.patch(f"/api/categories/{accessories['id']}",
                           json={"parent_id": phones["id"]})
    assert r.status_code == 422 and "subcategories" in r.json()["detail"]

    # Not its own parent
    r = owner_client.patch(f"/api/categories/{phones['id']}", json={"parent_id": phones["id"]})
    assert r.status_code == 422

    assert owner_client.post("/api/categories", json={"name": "X", "parent_id": 999}).status_code == 422

    names = [c["name"] for c in staff_client.get("/api/categories").json()]
    assert names == ["Accessories", "Chargers", "Phones"]


def test_category_rename_move_and_turn_off(owner_client):
    a = owner_client.post("/api/categories", json={"name": "Laptops"}).json()
    b = owner_client.post("/api/categories", json={"name": "Computers"}).json()

    r = owner_client.patch(f"/api/categories/{a['id']}", json={"parent_id": b["id"]})
    assert r.status_code == 200 and r.json()["parent_name"] == "Computers"
    r = owner_client.patch(f"/api/categories/{a['id']}", json={"parent_id": None})
    assert r.status_code == 200 and r.json()["parent_id"] is None

    assert owner_client.patch(f"/api/categories/{a['id']}",
                              json={"name": "computers"}).status_code == 409
    assert owner_client.patch(f"/api/categories/{a['id']}", json={"name": None}).status_code == 422
    assert owner_client.patch(f"/api/categories/{a['id']}",
                              json={"bogus": 1}).status_code == 422

    r = owner_client.patch(f"/api/categories/{a['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert [c["name"] for c in owner_client.get("/api/categories").json()] == ["Computers"]
    everything = owner_client.get("/api/categories", params={"include_inactive": True}).json()
    assert {c["name"] for c in everything} == {"Laptops", "Computers"}

    assert owner_client.patch("/api/categories/999", json={"name": "Nope"}).status_code == 404

    logs = owner_client.get("/api/audit-logs", params={"action": "category"}).json()["items"]
    assert {e["action"] for e in logs} == {"category.created", "category.updated"}


def test_brands(owner_client, staff_client):
    r = owner_client.post("/api/brands", json={"name": "Samsung"})
    assert r.status_code == 201
    brand = r.json()
    assert owner_client.post("/api/brands", json={"name": "samsung"}).status_code == 409
    assert owner_client.post("/api/brands", json={"name": "A"}).status_code == 422

    r = owner_client.patch(f"/api/brands/{brand['id']}", json={"name": "Samsung Electronics"})
    assert r.status_code == 200 and r.json()["name"] == "Samsung Electronics"

    owner_client.post("/api/brands", json={"name": "Apple"})
    assert [b["name"] for b in staff_client.get("/api/brands").json()] == [
        "Apple", "Samsung Electronics"]

    owner_client.patch(f"/api/brands/{brand['id']}", json={"is_active": False})
    assert [b["name"] for b in staff_client.get("/api/brands").json()] == ["Apple"]

    logs = owner_client.get("/api/audit-logs", params={"action": "brand"}).json()["items"]
    assert logs[0]["action"] == "brand.updated"
    assert logs[0]["details"] == {"is_active": {"from": "True", "to": "False"}}
