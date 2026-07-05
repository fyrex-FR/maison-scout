from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.auth import create_token
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import InviteCode, User


def _client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        with SessionLocal() as session:
            yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    settings.crawl_secret = "test-secret"
    settings.admin_emails = ""
    client = TestClient(app)
    return client, SessionLocal


def _user(db: Session, email: str = "x@example.com", is_admin: bool = False) -> User:
    user = User(email=email, display_name=email.split("@")[0], password_hash="hash", is_admin=is_admin)
    db.add(user)
    db.flush()
    return user


ADMIN_ENDPOINTS = [
    ("GET", "/api/admin/users"),
    ("GET", "/api/admin/invite-codes"),
]


def test_non_admin_gets_403_on_all_admin_endpoints():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        user = _user(db)
        db.commit()
        token = create_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/admin/users", headers=headers).status_code == 403
    assert client.get("/api/admin/invite-codes", headers=headers).status_code == 403
    assert client.post("/api/admin/invite-codes", headers=headers, json={}).status_code == 403
    assert client.patch("/api/admin/invite-codes/1", headers=headers, json={"active": False}).status_code == 403


def test_is_admin_flag_grants_access():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        admin = _user(db, "admin@example.com", is_admin=True)
        other = _user(db, "other@example.com")
        db.commit()
        token = create_token(admin)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/admin/users", headers=headers)
    assert response.status_code == 200
    emails = {row["email"] for row in response.json()}
    assert emails == {"admin@example.com", "other@example.com"}
    admin_row = next(row for row in response.json() if row["email"] == "admin@example.com")
    assert admin_row["is_admin"] is True

    response = client.get("/api/admin/invite-codes", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_admin_email_setting_grants_access_without_db_flag():
    client, SessionLocal = _client()
    settings.admin_emails = "boss@example.com"
    with SessionLocal() as db:
        user = _user(db, "boss@example.com", is_admin=False)
        db.commit()
        token = create_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["is_admin"] is True

    response = client.get("/api/admin/users", headers=headers)
    assert response.status_code == 200
    settings.admin_emails = ""


def test_admin_can_create_and_disable_invite_code():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        admin = _user(db, "admin@example.com", is_admin=True)
        db.commit()
        token = create_token(admin)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/api/admin/invite-codes", headers=headers, json={"note": "Pour Bob"})
    assert response.status_code == 200
    created = response.json()
    assert created["active"] is True
    assert created["note"] == "Pour Bob"
    assert created["used_count"] == 0
    assert len(created["code"]) == 8

    with SessionLocal() as db:
        stored = db.scalar(select(InviteCode).where(InviteCode.id == created["id"]))
        assert stored is not None
        assert stored.code == created["code"]

    response = client.patch(
        f"/api/admin/invite-codes/{created['id']}",
        headers=headers,
        json={"active": False},
    )
    assert response.status_code == 200
    assert response.json()["active"] is False

    response = client.patch(
        "/api/admin/invite-codes/999999",
        headers=headers,
        json={"active": False},
    )
    assert response.status_code == 404


def test_register_with_active_db_invite_code_succeeds_and_increments_used_count():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        invite = InviteCode(code="WELCOME1", active=True, note=None)
        db.add(invite)
        db.commit()
        invite_id = invite.id

    response = client.post(
        "/api/auth/register",
        json={
            "email": "newfriend@example.com",
            "password": "supersecret",
            "invite_code": "WELCOME1",
        },
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "newfriend@example.com"

    with SessionLocal() as db:
        stored = db.get(InviteCode, invite_id)
        assert stored.used_count == 1


def test_register_with_unknown_code_when_codes_exist_is_rejected():
    client, SessionLocal = _client()
    with SessionLocal() as db:
        db.add(InviteCode(code="KNOWNCODE", active=True))
        db.commit()

    response = client.post(
        "/api/auth/register",
        json={
            "email": "intruder@example.com",
            "password": "supersecret",
            "invite_code": "NOT-A-REAL-CODE",
        },
    )
    assert response.status_code == 403


def test_register_with_inactive_db_code_is_rejected_when_another_code_is_active():
    """An inactive code must not itself grant access once invitation is
    required (i.e. as soon as at least one *active* code exists elsewhere).
    """
    client, SessionLocal = _client()
    with SessionLocal() as db:
        db.add(InviteCode(code="DISABLED1", active=False))
        db.add(InviteCode(code="ACTIVE1", active=True))
        db.commit()

    response = client.post(
        "/api/auth/register",
        json={
            "email": "intruder2@example.com",
            "password": "supersecret",
            "invite_code": "DISABLED1",
        },
    )
    assert response.status_code == 403
