"""Importing old sales from another app (history only)."""
import io
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Customer, Product, Sale, StockMovement

SALES_HEAD = ["Date", "Vyapar invoice no.", "Customer", "Phone", "Items total (₦)", "Discount (₦)",
              "Sale total (₦)", "Paid (₦)", "Paid by", "Still owed (₦)", "Note"]
ITEMS_HEAD = ["Vyapar invoice no.", "Product name", "Quantity", "Price each (₦)", "Amount (₦)"]
PRODUCTS_HEAD = ["Product name", "Type", "Selling price (₦, last price)", "Cost price each (₦) — FILL IN"]

SALES = [
    [date(2026, 4, 2), 2, "Uncle Abdulrahman", None, 52500, 15500, 37000, 37000, "Bank transfer", 0, "Faisal"],
    [date(2026, 5, 1), 69, "Unknown", None, 500, 0, 500, 0, None, 500, None],
    [date(2026, 5, 26), 144, "Nura mahe", "08060728807", 680000, 0, 680000, 500000, "Bank transfer", 180000, None],
    [date(2026, 6, 10), 141, "Unknown", None, 5000, 0, 5000, 5000, "Cash", 0, None],
    ["Total", None, None, None, None, None, 722500, None, None, None, None],
]
ITEMS = [
    [2, "Samsung 35W", 5, 6000, 30000], [2, "Samsung 25W", 5, 4500, 22500],
    [69, "HD glass", 1, 500, 500],
    [144, "iPhone 13 Pro Max 256", 1, 680000, 680000],
    [141, "Printing picture on phone", 1, 5000, 5000],
    ["Total", None, 13, None, None],
]
PRODUCTS = [["HD glass", "Accessory", 1000, 250], ["iPhone 13 Pro Max 256", "Phone", 680000, 600000],
            ["Printing picture on phone", "Service", 3500, None], ["Samsung 25W", "Accessory", 4500, None],
            ["Samsung 35W", "Accessory", 6000, 3000]]


def book(sales=SALES, items=ITEMS, products=PRODUCTS) -> bytes:
    wb = Workbook()
    wb.active.title = "How to fill"
    for title, head, rows in (("Products", PRODUCTS_HEAD, products), ("Sales", SALES_HEAD, sales),
                              ("Sale items", ITEMS_HEAD, items)):
        if rows is None:
            continue
        ws = wb.create_sheet(title)
        ws.append(head)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, content, commit=False):
    return client.post("/api/sales-import", params={"commit": str(commit).lower()},
                       files={"file": ("old-sales.xlsx", content, "application/octet-stream")})


def test_preview_checks_and_saves_nothing(owner_client):
    r = upload(owner_client, book())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] is False and body["problems"] == []
    s = body["summary"]
    assert s["sales"] == 4 and s["total"] == "722500.00" and s["paid"] == "542000.00" and s["owed"] == "180500.00"
    assert s["date_from"] == "2026-04-02" and s["date_to"] == "2026-06-10"
    assert len(s["new_products"]) == 5 and s["products_without_cost"] == 2
    assert s["new_customers"] == ["Nura mahe", "Uncle Abdulrahman", "Unknown (old records)"]
    assert s["customers_owing"][0] == {"name": "Nura mahe", "owed": "180000.00"}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Sale)) == 0


def test_import_records_history_without_touching_stock(owner_client):
    r = upload(owner_client, book(), commit=True)
    assert r.status_code == 200 and r.json()["saved"] is True, r.text
    sales = {s["receipt_no"]: s for s in owner_client.get("/api/sales", params={"page_size": 50}).json()["items"]}
    assert set(sales) == {"VY-2", "VY-69", "VY-144", "VY-141"}
    vy2 = owner_client.get(f"/api/sales/{sales['VY-2']['id']}").json()
    assert vy2["business_date"] == "2026-04-02" and vy2["total"] == "37000.00" and vy2["discount_total"] == "15500.00"
    assert sum(Decimal(line["discount"]) for line in vy2["lines"]) == Decimal("15500")
    assert vy2["payments"][0]["method_name"] == "Bank transfer" and vy2["customer_name"] == "Uncle Abdulrahman"
    assert "invoice 2" in vy2["notes"] and "Faisal" in vy2["notes"]
    assert Decimal(vy2["profit"]) == Decimal("37000") - 5 * 3000  # Samsung 25W has no cost yet
    assert sales["VY-141"]["is_walk_in"] is True
    assert sales["VY-69"]["customer_name"] == "Unknown (old records)" and sales["VY-69"]["balance_due"] == "500.00"
    nura = next(c for c in owner_client.get("/api/customers", params={"q": "Nura"}).json()["items"])
    assert nura["phone"] == "08060728807" and nura["owed"] == "180000.00"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(StockMovement)) == 0  # history only
        types = dict(db.execute(select(Product.name, Product.tracking_type)
                                .where(Product.is_quick_sale.is_(False))).all())
        assert types["iPhone 13 Pro Max 256"] == "serialized" and types["Printing picture on phone"] == "service"
        assert types["HD glass"] == "quantity"
        assert db.scalar(select(Product.qty_on_hand).where(Product.name == "HD glass")) == 0
    may = owner_client.get("/api/dashboard", params={"date_from": "2026-05-01", "date_to": "2026-05-31"}).json()["period"]
    assert may["sales_count"] == 2 and may["net_sales"] == "680500.00"
    # Can't be imported twice.
    again = upload(owner_client, book(), commit=True).json()
    assert again["saved"] is False and "already imported" in again["problems"][0]["message"]
    # Imported history can't be returned or reversed (nothing came out of stock).
    r = owner_client.post(f"/api/sales/{sales['VY-141']['id']}/reverse",
                          json={"reason": "Testing it", "refund_method_id": 1})
    assert r.status_code == 409 and "imported" in r.json()["detail"]
    line_id = owner_client.get(f"/api/sales/{sales['VY-141']['id']}").json()["lines"][0]["id"]
    r = owner_client.post(f"/api/sales/{sales['VY-141']['id']}/returns", json={
        "lines": [{"sale_line_id": line_id}], "reason": "Testing it", "refund_method_id": 1,
        "idempotency_key": "i" * 32})
    assert r.status_code == 409
    # The debt is paid off the usual way.
    r = owner_client.post("/api/customer-payments", json={
        "customer_id": nura["id"], "amount": "180000", "method_id": 1, "idempotency_key": "p" * 32})
    assert r.status_code == 201, r.text
    assert owner_client.get(f"/api/customers/{nura['id']}").json()["owed"] == "0.00"


def test_existing_products_and_customers_are_reused(owner_client):
    owner_client.post("/api/products", json={"name": "hd  GLASS", "tracking_type": "quantity",
                                             "selling_price": "1000", "opening_quantity": 10,
                                             "opening_unit_cost": "200"})
    owner_client.post("/api/customers", json={"name": "Someone", "phone": "0806 072 8807"})
    body = upload(owner_client, book(products=None), commit=True).json()
    assert body["saved"], body
    assert "HD glass" not in body["summary"]["new_products"] and "Nura mahe" not in body["summary"]["new_customers"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Customer).where(Customer.phone == "08060728807")) == 1
        assert db.scalar(select(Product.qty_on_hand).where(func.lower(Product.name) == "hd glass")) == 10


def test_every_problem_is_listed_and_nothing_saved(owner_client):
    sales = [row[:] for row in SALES]
    sales[0][6] = 40000               # total doesn't match the items
    sales[2][9] = 1                   # paid + owed != total
    sales[3][8] = "Opay"              # not a payment method
    items = ITEMS + [[999, "Ghost", 1, 100, 100], [2, "", 1, 1, 1]]
    body = upload(owner_client, book(sales=sales, items=items), commit=True).json()
    msgs = " | ".join(f"{p['where']}: {p['message']}" for p in body["problems"])
    assert body["saved"] is False
    assert "Invoice 2" in msgs and "discount" in msgs
    assert "Invoice 144" in msgs and "still owed" in msgs
    assert "Opay" in msgs and "Bank transfer" in msgs
    assert "Invoice 999 is not on the Sales sheet" in msgs and "Product name is empty" in msgs
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Sale)) == 0


def test_bad_files(owner_client, staff_client):
    assert upload(owner_client, b"not excel").status_code == 422
    r = upload(owner_client, book(items=None))
    assert r.status_code == 422 and "Sale items" in r.json()["detail"]
    assert upload(staff_client, book()).status_code == 403
