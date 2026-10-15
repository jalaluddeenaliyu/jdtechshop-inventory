from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.database import SessionLocal, engine
from app.models import AuditLog, User
from tests.conftest import PASSWORD, logged_in, new_client


def test_health_is_public_and_reports_database(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] == "ok"


def test_login_success_sets_httponly_cookie_and_hides_hash(client, owner):
    r = client.post("/api/auth/login", json={"username": "OWNER ", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "owner" and body["role"] == "owner"
    assert "password_hash" not in body and "password" not in body
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert client.get("/api/auth/me").json()["username"] == "owner"


def test_wrong_password_and_unknown_user_give_same_message(client, owner):
    a = client.post("/api/auth/login", json={"username": "owner", "password": "wrong-pass1"})
    b = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-pass1"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_protected_endpoint_requires_login(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/settings").status_code == 401


def test_tampered_token_rejected(client, owner):
    client.cookies.set("jdt_session", "not.a.real.token", path="/api")
    assert client.get("/api/auth/me").status_code == 401


def test_logout_clears_session(owner):
    c = logged_in("owner")
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/auth/me").status_code == 401


def test_account_locks_after_repeated_failures(client, staff):
    for _ in range(5):
        r = client.post("/api/auth/login", json={"username": "cashier", "password": "bad-guess1"})
        assert r.status_code == 401
    # Even the correct password is refused while locked.
    r = client.post("/api/auth/login", json={"username": "cashier", "password": PASSWORD})
    assert r.status_code == 429
    # After the lock expires, login works again.
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.username == "cashier"))
        u.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.post("/api/auth/login",
                       json={"username": "cashier", "password": PASSWORD}).status_code == 200


def test_mutating_request_without_client_header_is_blocked(owner):
    from fastapi.testclient import TestClient

    from app.main import app

    bare = TestClient(app)  # simulates a cross-site form post: no custom header
    r = bare.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 403


def test_change_own_password_logs_out_other_devices(staff):
    phone = logged_in("cashier")
    laptop = logged_in("cashier")
    r = laptop.post("/api/auth/change-password",
                    json={"current_password": PASSWORD, "new_password": "NewSecret123"})
    assert r.status_code == 200
    assert laptop.get("/api/auth/me").status_code == 200  # this device stays in
    assert phone.get("/api/auth/me").status_code == 401   # other device is out
    assert new_client().post("/api/auth/login", json={"username": "cashier",
                             "password": "NewSecret123"}).status_code == 200


def test_change_password_rejects_wrong_current_and_weak_new(staff_client):
    r = staff_client.post("/api/auth/change-password",
                          json={"current_password": "wrong-one1", "new_password": "NewSecret123"})
    assert r.status_code == 400
    r = staff_client.post("/api/auth/change-password",
                          json={"current_password": PASSWORD, "new_password": "short"})
    assert r.status_code == 422
    assert "at least 8" in r.json()["detail"]


def test_logins_are_audited_without_passwords(client, owner):
    client.post("/api/auth/login", json={"username": "owner", "password": "bad-guess1"})
    client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    with SessionLocal() as db:
        actions = [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]
        assert "auth.login_failed" in actions and "auth.login" in actions
        dump = str([a.details for a in db.scalars(select(AuditLog))])
        assert PASSWORD not in dump and "bad-guess1" not in dump


def test_audit_log_cannot_be_edited_or_deleted(owner_client):
    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE audit_logs SET action = 'tampered'"))
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM audit_logs"))
