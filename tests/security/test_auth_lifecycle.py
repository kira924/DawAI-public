import asyncio
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.database import get_db
from src.core.security import get_password_hash
from src.main import app
from src.modules.auth import service as auth_service
from src.modules.auth.models import SupportAccessAudit
from src.modules.tenants.models import Tenant
from src.modules.users.models import User
from src.scripts.bootstrap_super_admin import bootstrap_super_admin

PASSWORD = "Synthetic-auth-password-123"
NEW_PASSWORD = "Synthetic-new-password-456"


def _request(
    method: str,
    path: str,
    database_url: str,
    *,
    form_data: dict[str, str] | None = None,
    json_body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    engine = create_engine(database_url)

    def override_database() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(
                method,
                path,
                data=form_data,
                json=json_body,
                headers=headers,
            )

    app.dependency_overrides[get_db] = override_database
    try:
        return asyncio.run(request())
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _seed_accounts(database_url: str) -> dict[str, object]:
    engine = create_engine(database_url)
    password_hash = get_password_hash(PASSWORD)
    with Session(engine) as db:
        tenant = Tenant(
            name="Auth Lifecycle Pharmacy",
            owner_email="auth-owner@example.com",
            is_active=False,
        )
        platform_tenant = Tenant(
            name="Auth Platform Container",
            owner_email="auth-platform@example.com",
            is_active=False,
        )
        db.add_all([tenant, platform_tenant])
        db.flush()
        owner = User(
            email="auth-owner@example.com",
            hashed_password=password_hash,
            full_name="Auth Owner",
            role="manager",
            is_active=True,
            tenant_id=tenant.id,
        )
        pharmacist = User(
            email="auth-pharmacist@example.com",
            hashed_password=password_hash,
            full_name="Auth Pharmacist",
            role="pharmacist",
            is_active=True,
            tenant_id=tenant.id,
        )
        other_manager = User(
            email="auth-manager@example.com",
            hashed_password=password_hash,
            full_name="Auth Manager",
            role="manager",
            is_active=True,
            tenant_id=tenant.id,
        )
        support_user = User(
            email="auth-support@example.com",
            hashed_password=password_hash,
            full_name="Auth Support",
            role="super_admin",
            is_active=True,
            tenant_id=platform_tenant.id,
        )
        db.add_all([owner, pharmacist, other_manager, support_user])
        db.flush()
        tenant.owner_user_id = owner.id
        tenant.is_active = True
        db.commit()
        result = {
            "tenant_id": tenant.id,
            "owner_id": owner.id,
            "pharmacist_id": pharmacist.id,
            "other_manager_id": other_manager.id,
            "support_id": support_user.id,
            "owner_email": owner.email,
            "pharmacist_email": pharmacist.email,
            "other_manager_email": other_manager.email,
            "support_email": support_user.email,
        }
    engine.dispose()
    return result


def _login(database_url: str, email: str, password: str = PASSWORD) -> httpx.Response:
    return _request(
        "POST",
        "/api/auth/login",
        database_url,
        form_data={"username": email, "password": password},
    )


def _bearer(login_response: httpx.Response) -> dict[str, str]:
    return {"Authorization": f"Bearer {login_response.json()['access_token']}"}


def _token_from_message(body: str) -> str:
    link = body.strip().splitlines()[-1]
    return parse_qs(urlparse(link).query)["token"][0]


@pytest.mark.integration
def test_refresh_rotation_replay_and_logout(migrated_postgresql_database_url: str) -> None:
    accounts = _seed_accounts(migrated_postgresql_database_url)
    login = _login(migrated_postgresql_database_url, str(accounts["pharmacist_email"]))
    assert login.status_code == 200
    assert login.json()["expires_in"] == 900
    assert login.json()["refresh_token"]

    refresh = _request(
        "POST",
        "/api/auth/refresh",
        migrated_postgresql_database_url,
        json_body={"refresh_token": login.json()["refresh_token"]},
    )
    assert refresh.status_code == 200
    assert refresh.json()["refresh_token"] != login.json()["refresh_token"]

    old_access = _request(
        "GET",
        "/api/products/",
        migrated_postgresql_database_url,
        headers=_bearer(login),
    )
    assert old_access.status_code == 401

    replay = _request(
        "POST",
        "/api/auth/refresh",
        migrated_postgresql_database_url,
        json_body={"refresh_token": login.json()["refresh_token"]},
    )
    assert replay.status_code == 401

    revoked_family_access = _request(
        "GET",
        "/api/products/",
        migrated_postgresql_database_url,
        headers=_bearer(refresh),
    )
    assert revoked_family_access.status_code == 401

    second_login = _login(migrated_postgresql_database_url, str(accounts["pharmacist_email"]))
    logout = _request(
        "POST",
        "/api/auth/logout",
        migrated_postgresql_database_url,
        headers=_bearer(second_login),
    )
    assert logout.status_code == 200
    assert (
        _request(
            "GET",
            "/api/products/",
            migrated_postgresql_database_url,
            headers=_bearer(second_login),
        ).status_code
        == 401
    )


@pytest.mark.integration
def test_login_throttling_blocks_valid_credentials_after_limit(
    migrated_postgresql_database_url: str,
) -> None:
    accounts = _seed_accounts(migrated_postgresql_database_url)
    email = str(accounts["pharmacist_email"])

    statuses = [
        _login(migrated_postgresql_database_url, email, "Wrong-password-value").status_code
        for _ in range(settings.LOGIN_MAX_FAILURES)
    ]

    assert statuses[:-1] == [401] * (settings.LOGIN_MAX_FAILURES - 1)
    assert statuses[-1] == 429
    assert _login(migrated_postgresql_database_url, email).status_code == 429


@pytest.mark.integration
def test_self_service_profile_and_password_change(migrated_postgresql_database_url: str) -> None:
    accounts = _seed_accounts(migrated_postgresql_database_url)
    email = str(accounts["pharmacist_email"])
    login = _login(migrated_postgresql_database_url, email)

    profile = _request(
        "PATCH",
        "/api/account/me",
        migrated_postgresql_database_url,
        headers=_bearer(login),
        json_body={"full_name": "Updated Pharmacist"},
    )
    assert profile.status_code == 200
    assert profile.json()["full_name"] == "Updated Pharmacist"

    wrong_password = _request(
        "POST",
        "/api/account/change-password",
        migrated_postgresql_database_url,
        headers=_bearer(login),
        json_body={"current_password": "Wrong-password-value", "new_password": NEW_PASSWORD},
    )
    assert wrong_password.status_code == 400

    changed = _request(
        "POST",
        "/api/account/change-password",
        migrated_postgresql_database_url,
        headers=_bearer(login),
        json_body={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200
    assert _login(migrated_postgresql_database_url, email).status_code == 401
    assert _login(migrated_postgresql_database_url, email, NEW_PASSWORD).status_code == 200


@pytest.mark.integration
def test_password_reset_and_email_change_are_one_time_and_revoke_sessions(
    migrated_postgresql_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = _seed_accounts(migrated_postgresql_database_url)
    email = str(accounts["pharmacist_email"])
    sent_messages: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "no-reply@example.com")
    monkeypatch.setattr(
        auth_service,
        "send_account_email",
        lambda recipient, _subject, body: sent_messages.append((recipient, body)),
    )

    reset_request = _request(
        "POST",
        "/api/auth/password-reset/request",
        migrated_postgresql_database_url,
        json_body={"email": email},
    )
    assert reset_request.status_code == 202
    reset_token = _token_from_message(sent_messages[-1][1])
    reset = _request(
        "POST",
        "/api/auth/password-reset/confirm",
        migrated_postgresql_database_url,
        json_body={"token": reset_token, "new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 200
    assert (
        _request(
            "POST",
            "/api/auth/password-reset/confirm",
            migrated_postgresql_database_url,
            json_body={"token": reset_token, "new_password": PASSWORD},
        ).status_code
        == 400
    )

    login = _login(migrated_postgresql_database_url, email, NEW_PASSWORD)
    new_email = "changed-pharmacist@example.com"
    email_request = _request(
        "POST",
        "/api/account/email-change/request",
        migrated_postgresql_database_url,
        headers=_bearer(login),
        json_body={"current_password": NEW_PASSWORD, "new_email": new_email},
    )
    assert email_request.status_code == 200
    assert sent_messages[-1][0] == new_email
    email_token = _token_from_message(sent_messages[-1][1])
    confirmation = _request(
        "POST",
        "/api/auth/email-change/confirm",
        migrated_postgresql_database_url,
        json_body={"token": email_token},
    )
    assert confirmation.status_code == 200
    assert _login(migrated_postgresql_database_url, email, NEW_PASSWORD).status_code == 401
    assert _login(migrated_postgresql_database_url, new_email, NEW_PASSWORD).status_code == 200


@pytest.mark.integration
def test_first_super_admin_bootstrap_is_one_time(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        created = bootstrap_super_admin(
            db,
            email="first-platform@example.com",
            full_name="First Platform Admin",
            password=PASSWORD,
        )
        assert created.role == "super_admin"
        assert created.tenant.is_active is False
        with pytest.raises(RuntimeError, match="already exists"):
            bootstrap_super_admin(
                db,
                email="second-platform@example.com",
                full_name="Second Platform Admin",
                password=PASSWORD,
            )
    engine.dispose()


@pytest.mark.integration
def test_manager_reset_boundary_and_one_time_bootstrap(
    migrated_postgresql_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = _seed_accounts(migrated_postgresql_database_url)
    sent_messages: list[str] = []
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "no-reply@example.com")
    monkeypatch.setattr(
        auth_service,
        "send_account_email",
        lambda _recipient, _subject, body: sent_messages.append(body),
    )
    manager_login = _login(migrated_postgresql_database_url, str(accounts["other_manager_email"]))
    reset_pharmacist = _request(
        "POST",
        f"/api/users/{accounts['pharmacist_id']}/password-reset",
        migrated_postgresql_database_url,
        headers=_bearer(manager_login),
    )
    assert reset_pharmacist.status_code == 200
    reset_manager = _request(
        "POST",
        f"/api/users/{accounts['owner_id']}/password-reset",
        migrated_postgresql_database_url,
        headers=_bearer(manager_login),
    )
    assert reset_manager.status_code == 404

    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="already exists"):
            bootstrap_super_admin(
                db,
                email="another-platform@example.com",
                full_name="Another Platform Admin",
                password=PASSWORD,
            )
    engine.dispose()


@pytest.mark.integration
def test_support_access_is_owner_granted_scoped_audited_and_revocable(
    migrated_postgresql_database_url: str,
) -> None:
    accounts = _seed_accounts(migrated_postgresql_database_url)
    owner_login = _login(migrated_postgresql_database_url, str(accounts["owner_email"]))
    manager_login = _login(migrated_postgresql_database_url, str(accounts["other_manager_email"]))
    support_login = _login(migrated_postgresql_database_url, str(accounts["support_email"]))

    denied_grant = _request(
        "POST",
        "/api/support/grants",
        migrated_postgresql_database_url,
        headers=_bearer(manager_login),
        json_body={
            "support_user_email": accounts["support_email"],
            "reason": "Synthetic support investigation",
        },
    )
    assert denied_grant.status_code == 403

    grant = _request(
        "POST",
        "/api/support/grants",
        migrated_postgresql_database_url,
        headers=_bearer(owner_login),
        json_body={
            "support_user_email": accounts["support_email"],
            "reason": "Synthetic support investigation",
        },
    )
    assert grant.status_code == 201
    grant_id = grant.json()["id"]
    support_headers = {**_bearer(support_login), "X-Support-Grant-ID": grant_id}

    assert (
        _request(
            "GET",
            "/api/products/",
            migrated_postgresql_database_url,
            headers=support_headers,
        ).status_code
        == 200
    )
    assert (
        _request(
            "POST",
            "/api/products/",
            migrated_postgresql_database_url,
            headers=support_headers,
            json_body={"name": "Denied Support Product", "price": 10},
        ).status_code
        == 403
    )
    assert (
        _request(
            "GET",
            "/api/users/",
            migrated_postgresql_database_url,
            headers=support_headers,
        ).status_code
        == 403
    )

    operations_grant = _request(
        "POST",
        "/api/support/grants",
        migrated_postgresql_database_url,
        headers=_bearer(owner_login),
        json_body={
            "support_user_email": accounts["support_email"],
            "scope": "operations",
            "reason": "Synthetic approved data repair",
        },
    )
    operations_headers = {
        **_bearer(support_login),
        "X-Support-Grant-ID": operations_grant.json()["id"],
    }
    product = _request(
        "POST",
        "/api/products/",
        migrated_postgresql_database_url,
        headers=operations_headers,
        json_body={"name": "Approved Support Product", "price": 10},
    )
    assert product.status_code == 201
    assert product.json()["tenant_id"] == accounts["tenant_id"]

    audit = _request(
        "GET",
        f"/api/support/grants/{grant_id}/audit",
        migrated_postgresql_database_url,
        headers=_bearer(owner_login),
    )
    assert audit.status_code == 200
    assert {event["event_type"] for event in audit.json()} >= {"grant_created", "request_started"}

    revoke = _request(
        "POST",
        f"/api/support/grants/{grant_id}/revoke",
        migrated_postgresql_database_url,
        headers=_bearer(owner_login),
    )
    assert revoke.status_code == 200
    assert (
        _request(
            "GET",
            "/api/products/",
            migrated_postgresql_database_url,
            headers=support_headers,
        ).status_code
        == 403
    )

    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        assert db.query(SupportAccessAudit).count() >= 7
    engine.dispose()
