"""Products: creation rules, editing, search, filters and low stock."""
import itertools

import pytest


# Product names are unique. Tests that make several default products get "… #2", "… #3";
# the first one in each test keeps the plain name (conftest clears this between tests).
DEFAULT_NAMES_USED: set[str] = set()


def _default_name() -> str:
    base = "Oraimo 20W charger"
    name = base
    for n in itertools.count(2):
        if name not in DEFAULT_NAMES_USED:
            break
        name = f"{base} #{n}"
    DEFAULT_NAMES_USED.add(name)
    return name


def make_product(client, **overrides) -> dict:
    body = {"name": overrides.get("name") or _default_name(), "tracking_type": "quantity",
            "selling_price": "6500"}
    body.update(overrides)
    r = client.post("/api/products", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def opening(client, product_id: int, quantity: int, unit_cost: str = "4000"):
    r = client.post(f"/api/products/{product_id}/opening-stock",
                    json={"quantity": quantity, "unit_cost": unit_cost})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_quantity_product_with_generated_sku(owner_client):
    p = make_product(owner_client, model="  OCW-E65S ", barcode="", specifications="")
    assert p["sku"] == "JDT-00001"
    assert p["barcode"] is None and p["specifications"] is None
    assert p["model"] == "OCW-E65S"
    assert p["condition"] == "new"  # default for quantity items
    assert p["qty_on_hand"] == 0 and p["avg_cost"] == "0.0000"
    assert p["selling_price"] == "6500.00"
    assert p["effective_reorder_level"] == 3  # shop default
    assert p["is_low_stock"] is True  # nothing in stock yet
    assert p["opening_stock_open"] is True

    assert make_product(owner_client, name="Cable")["sku"] == "JDT-00002"

    logs = owner_client.get("/api/audit-logs", params={"action": "product"}).json()["items"]
    assert logs[-1]["action"] == "product.created" and logs[-1]["details"]["sku"] == "JDT-00001"


def test_sku_and_barcode_are_normalised_and_unique(owner_client):
    p = make_product(owner_client, sku=" ora-20w ", barcode="6941057400123")
    assert p["sku"] == "ORA-20W"

    r = owner_client.post("/api/products", json={"name": "Other", "tracking_type": "quantity",
                                                  "selling_price": "1", "sku": "ORA-20w"})
    assert r.status_code == 409 and "Oraimo 20W charger" in r.json()["detail"]
    r = owner_client.post("/api/products", json={"name": "Other", "tracking_type": "quantity",
                                                  "selling_price": "1",
                                                  "barcode": "6941057400123"})
    assert r.status_code == 409 and "barcode" in r.json()["detail"]

    r = owner_client.post("/api/products", json={"name": "Other", "tracking_type": "quantity",
                                                  "selling_price": "1", "sku": "has space"})
    assert r.status_code == 422 and "SKU" in r.json()["detail"]


def test_generated_sku_skips_one_typed_by_hand(owner_client):
    make_product(owner_client, sku="JDT-00001")
    assert make_product(owner_client, name="Second")["sku"] == "JDT-00002"


@pytest.mark.parametrize("field,value", [("qty_on_hand", 50), ("avg_cost", "100")])
def test_stock_and_cost_cannot_be_typed_in(owner_client, field, value):
    r = owner_client.post("/api/products", json={"name": "Sneaky", "tracking_type": "quantity",
                                                  "selling_price": "1", field: value})
    assert r.status_code == 422
    p = make_product(owner_client)
    assert owner_client.patch(f"/api/products/{p['id']}", json={field: value}).status_code == 422
    assert owner_client.get(f"/api/products/{p['id']}").json()["qty_on_hand"] == 0


def test_serialized_product_condition_lives_on_units(owner_client):
    r = owner_client.post("/api/products", json={"name": "iPhone 13 128GB",
                                                  "tracking_type": "serialized",
                                                  "selling_price": "650000", "condition": "used"})
    assert r.status_code == 422 and "each unit" in r.json()["detail"]
    p = make_product(owner_client, name="iPhone 13 128GB", tracking_type="serialized",
                     selling_price="650000")
    assert p["condition"] is None


def test_category_and_brand_must_exist_and_be_on(owner_client):
    cat = owner_client.post("/api/categories", json={"name": "Old stuff"}).json()
    owner_client.patch(f"/api/categories/{cat['id']}", json={"is_active": False})
    brand = owner_client.post("/api/brands", json={"name": "Tecno"}).json()

    base = {"name": "Spark 10", "tracking_type": "serialized", "selling_price": "120000"}
    r = owner_client.post("/api/products", json={**base, "category_id": cat["id"]})
    assert r.status_code == 422 and "turned off" in r.json()["detail"]
    r = owner_client.post("/api/products", json={**base, "category_id": 999})
    assert r.status_code == 422
    r = owner_client.post("/api/products", json={**base, "brand_id": 999})
    assert r.status_code == 422

    p = make_product(owner_client, **base, brand_id=brand["id"])
    assert p["brand_name"] == "Tecno" and p["category_name"] is None


def test_update_product_audits_price_change_and_rejects_nulls(owner_client):
    p = make_product(owner_client, barcode="12345678")
    r = owner_client.patch(f"/api/products/{p['id']}",
                           json={"selling_price": "7000.5", "barcode": None, "reorder_level": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["selling_price"] == "7000.50" and body["barcode"] is None
    assert body["reorder_level"] == 10 and body["effective_reorder_level"] == 10

    log = owner_client.get("/api/audit-logs", params={"action": "product.updated"}).json()["items"][0]
    assert log["details"]["selling_price"] == {"from": "6500.00", "to": "7000.50"}

    for field in ("name", "sku", "selling_price", "tracking_type"):
        r = owner_client.patch(f"/api/products/{p['id']}", json={field: None})
        assert r.status_code == 422, field
    r = owner_client.patch(f"/api/products/{p['id']}", json={"selling_price": "-1"})
    assert r.status_code == 422
    r = owner_client.patch(f"/api/products/{p['id']}", json={"selling_price": "1.234"})
    assert r.status_code == 422
    assert owner_client.patch("/api/products/999", json={"name": "Nope"}).status_code == 404


def test_tracking_type_locked_once_stock_has_moved(owner_client):
    p = make_product(owner_client, condition="refurbished")
    r = owner_client.patch(f"/api/products/{p['id']}", json={"tracking_type": "serialized"})
    assert r.status_code == 200 and r.json()["condition"] is None
    r = owner_client.patch(f"/api/products/{p['id']}", json={"tracking_type": "quantity"})
    assert r.status_code == 200 and r.json()["condition"] == "new"

    opening(owner_client, p["id"], 5)
    r = owner_client.patch(f"/api/products/{p['id']}", json={"tracking_type": "serialized"})
    assert r.status_code == 409 and "stock history" in r.json()["detail"]


def test_staff_can_look_up_products_but_never_sees_cost(owner_client, staff_client):
    p = make_product(owner_client)
    opening(owner_client, p["id"], 4, "4100")
    assert owner_client.get(f"/api/products/{p['id']}").json()["avg_cost"] == "4100.0000"

    seen = staff_client.get(f"/api/products/{p['id']}").json()
    assert seen["avg_cost"] is None and seen["qty_on_hand"] == 4
    assert all(i["avg_cost"] is None for i in staff_client.get("/api/products").json()["items"])
    assert staff_client.get("/api/products/999").status_code == 404


def test_search_filters_sorting_and_paging(owner_client):
    acc = owner_client.post("/api/categories", json={"name": "Accessories"}).json()
    chargers = owner_client.post("/api/categories", json={"name": "Chargers",
                                                           "parent_id": acc["id"]}).json()
    phones = owner_client.post("/api/categories", json={"name": "Phones"}).json()
    samsung = owner_client.post("/api/brands", json={"name": "Samsung"}).json()

    charger = make_product(owner_client, category_id=chargers["id"], selling_price="6500",
                           barcode="6941057400123")
    case = make_product(owner_client, name="Silicone case", category_id=acc["id"],
                        selling_price="2500", model="A54-CASE")
    phone = make_product(owner_client, name="Galaxy A54", tracking_type="serialized",
                         category_id=phones["id"], brand_id=samsung["id"], selling_price="420000")
    old = make_product(owner_client, name="Old charger 5W", selling_price="1000")
    owner_client.patch(f"/api/products/{old['id']}", json={"is_active": False})

    def names(**params):
        return [p["name"] for p in owner_client.get("/api/products", params=params).json()["items"]]

    assert names() == ["Galaxy A54", "Oraimo 20W charger", "Silicone case"]  # inactive hidden
    assert names(include_inactive=True) == ["Galaxy A54", "Old charger 5W",
                                            "Oraimo 20W charger", "Silicone case"]
    assert names(q="CHARGER") == ["Oraimo 20W charger"]
    assert names(q="a54-case") == ["Silicone case"]           # model
    assert names(q="7400123") == ["Oraimo 20W charger"]       # barcode
    assert names(q=charger["sku"]) == ["Oraimo 20W charger"]  # SKU
    assert names(q="%") == []                                 # wildcards are literal
    assert names(category_id=acc["id"]) == ["Oraimo 20W charger", "Silicone case"]  # + subcats
    assert names(category_id=chargers["id"]) == ["Oraimo 20W charger"]
    assert names(brand_id=samsung["id"]) == ["Galaxy A54"]
    assert names(tracking_type="serialized") == ["Galaxy A54"]
    assert names(sort="price", descending=True) == ["Galaxy A54", "Oraimo 20W charger",
                                                    "Silicone case"]

    page = owner_client.get("/api/products", params={"page_size": 2, "page": 2}).json()
    assert page["total"] == 3 and [p["name"] for p in page["items"]] == ["Silicone case"]

    # Stock filters: charger 10 (above default 3), case 2 (low), phone 0 (out)
    opening(owner_client, charger["id"], 10)
    opening(owner_client, case["id"], 2, "900")
    assert names(stock="in") == ["Oraimo 20W charger", "Silicone case"]
    assert names(stock="out") == ["Galaxy A54"]
    assert names(stock="low") == ["Galaxy A54", "Silicone case"]
    assert names(sort="qty") == ["Galaxy A54", "Silicone case", "Oraimo 20W charger"]
    assert phone["id"]

    assert owner_client.get("/api/products", params={"sort": "cost"}).status_code == 422


def test_low_stock_uses_product_level_or_shop_default(owner_client):
    p = make_product(owner_client)
    opening(owner_client, p["id"], 5)
    assert owner_client.get(f"/api/products/{p['id']}").json()["is_low_stock"] is False

    owner_client.patch(f"/api/products/{p['id']}", json={"reorder_level": 5})
    assert owner_client.get(f"/api/products/{p['id']}").json()["is_low_stock"] is True

    owner_client.patch(f"/api/products/{p['id']}", json={"reorder_level": None})
    settings = owner_client.get("/api/settings").json()
    owner_client.put("/api/settings", json={
        "business_name": settings["business_name"], "default_reorder_level": 8,
        "staff_max_discount_percent": settings["staff_max_discount_percent"],
        "allow_staff_credit_sales": settings["allow_staff_credit_sales"],
    })
    p = owner_client.get(f"/api/products/{p['id']}").json()
    assert p["effective_reorder_level"] == 8 and p["is_low_stock"] is True
