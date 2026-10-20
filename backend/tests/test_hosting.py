"""Checks for running on hosted platforms such as Render + Neon."""
from app.config import Settings


def test_ping_is_public_and_does_not_need_login(client):
    r = client.get("/api/ping")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_database_url_prefix_is_fixed_automatically():
    neon = "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    s = Settings(database_url=neon, test_database_url="postgres://u:p@h/t")
    assert s.database_url.startswith("postgresql+psycopg://u:p@ep-x")
    assert s.database_url.endswith("?sslmode=require")
    assert s.test_database_url == "postgresql+psycopg://u:p@h/t"
    already = "postgresql+psycopg://localhost/x"
    assert Settings(database_url=already).database_url == already
