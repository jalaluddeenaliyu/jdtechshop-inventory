"""Test setup.

Tests run against a real PostgreSQL database (TEST_DATABASE_URL), never the shop's data.
The schema is built with the real Alembic migrations, so migrations are tested too.
Each test starts with empty tables plus the default settings and payment methods.
"""
import os

os.environ["ENVIRONMENT"] = "test"
os.environ["BCRYPT_ROUNDS"] = "4"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import BACKEND_DIR, get_settings  # noqa: E402

get_settings.cache_clear()
settings = get_settings()
assert settings.environment == "test"
assert settings.test_database_url != settings.database_url, (
    "TEST_DATABASE_URL must be different from DATABASE_URL — tests wipe their database."
)

from sqlalchemy import event  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402


# The test database is thrown away, so don't wait for each commit to reach the disk.
# This halves the suite's run time; the shop's database is never touched.
@event.listens_for(engine, "connect")
def _fast_commits(dbapi_conn, _record):
    with dbapi_conn.cursor() as cur:
        cur.execute("SET synchronous_commit = off")
    dbapi_conn.commit()
from app.main import app  # noqa: E402
from app.schemas.user import UserCreate  # noqa: E402
from app.services import users as user_service  # noqa: E402

PASSWORD = "CorrectHorse9"


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.test_database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _reset_default_product_names():
    from tests import test_products

    test_products.DEFAULT_NAMES_USED.clear()
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    with engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE expenses, stock_adjustment_lines, stock_adjustments, sale_return_lines, sale_returns, "
            "supplier_return_lines, supplier_returns, customer_payment_allocations, customer_payments, sale_lines, sales, receipt_counters, "
            "customers, supplier_payments, purchase_line_units, purchase_lines, purchases, "
            "suppliers, imei_registry, product_units, stock_movements, products, categories, "
            "brands, audit_logs, business_settings, payment_methods, users "
            "RESTART IDENTITY CASCADE"
        ))
        conn.execute(text("ALTER SEQUENCE product_sku_seq RESTART"))
        conn.execute(text("ALTER SEQUENCE purchase_ref_seq RESTART"))
        conn.execute(text(
            "INSERT INTO business_settings (id, business_name, slogan, address, receipt_footer, "
            "currency_code, default_reorder_level, staff_max_discount_percent, "
            "allow_staff_credit_sales) VALUES (1, 'JD TechShop', 'Delivering Happiness', "
            "'Sokoto, Nigeria', 'Thank you', 'NGN', 3, 5.00, true)"
        ))
        conn.execute(text(
            "INSERT INTO customers (name, is_walk_in, is_active) "
            "VALUES ('Walk-in customer', true, true)"
        ))
        conn.execute(text(
            "INSERT INTO payment_methods (name, is_active, sort_order) VALUES "
            "('Cash', true, 1), ('Bank transfer', true, 2), ('POS terminal', true, 3)"
        ))
    yield


def make_user(username: str, role: str = "staff", full_name: str | None = None):
    with SessionLocal() as db:
        return user_service.create_user(
            db,
            UserCreate(full_name=full_name or username.title(), username=username,
                       password=PASSWORD, role=role),
            actor=None, request=None,
        )


def new_client() -> TestClient:
    return TestClient(app, headers={"X-JDT-Client": "web"})


def logged_in(username: str, password: str = PASSWORD) -> TestClient:
    c = new_client()
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def client() -> TestClient:
    return new_client()


@pytest.fixture
def owner():
    return make_user("owner", "owner", "Jalal Owner")


@pytest.fixture
def staff():
    return make_user("cashier", "staff", "Amina Cashier")


@pytest.fixture
def owner_client(owner) -> TestClient:
    return logged_in("owner")


@pytest.fixture
def staff_client(staff) -> TestClient:
    return logged_in("cashier")
