import asyncio
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.core.security import get_password_hash
from src.main import app
from src.modules.tenants.models import Tenant
from src.modules.users.models import User

PASSWORD = "Synthetic-test-password-123"


def _request(
    method: str,
    path: str,
    database_url: str,
    *,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    engine = create_engine(database_url)

    def override_database() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, data=data, headers=headers)

    app.dependency_overrides[get_db] = override_database
    try:
        return asyncio.run(request())
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _seed_account(
    database_url: str, *, user_is_active: bool = True, tenant_is_active: bool = True
) -> tuple[int, int]:
    engine = create_engine(database_url)
    with Session(engine) as db:
        tenant = Tenant(
            name="Synthetic Pharmacy",
            owner_email="owner@example.test",
            is_active=False,
        )
        db.add(tenant)
        db.flush()

        owner = User(
            email="owner-status@example.test",
            hashed_password=get_password_hash(PASSWORD),
            full_name="Synthetic Owner",
            role="manager",
            is_active=True,
            tenant_id=tenant.id,
        )
        user = User(
            email="pharmacist@example.test",
            hashed_password=get_password_hash(PASSWORD),
            full_name="Synthetic Pharmacist",
            role="pharmacist",
            is_active=user_is_active,
            tenant_id=tenant.id,
        )
        db.add_all([owner, user])
        db.flush()
        tenant.owner_user_id = owner.id
        tenant.owner_email = owner.email
        tenant.is_active = tenant_is_active
        db.commit()
        user_id = user.id
        tenant_id = tenant.id

    engine.dispose()
    return user_id, tenant_id


def _login(database_url: str) -> httpx.Response:
    return _request(
        "POST",
        "/api/auth/login",
        database_url,
        data={"username": "pharmacist@example.test", "password": PASSWORD},
    )


@pytest.mark.integration
def test_active_account_can_log_in(migrated_postgresql_database_url: str) -> None:
    _seed_account(migrated_postgresql_database_url)

    response = _login(migrated_postgresql_database_url)

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("user_is_active", "tenant_is_active"),
    [(False, True), (True, False)],
)
def test_inactive_account_cannot_log_in(
    migrated_postgresql_database_url: str,
    user_is_active: bool,
    tenant_is_active: bool,
) -> None:
    _seed_account(
        migrated_postgresql_database_url,
        user_is_active=user_is_active,
        tenant_is_active=tenant_is_active,
    )

    response = _login(migrated_postgresql_database_url)

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect email or password"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.integration
@pytest.mark.parametrize("disable_target", ["user", "tenant"])
def test_disabling_account_invalidates_existing_access_token(
    migrated_postgresql_database_url: str,
    disable_target: str,
) -> None:
    user_id, tenant_id = _seed_account(migrated_postgresql_database_url)
    login_response = _login(migrated_postgresql_database_url)
    token = login_response.json()["access_token"]

    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        if disable_target == "user":
            user = db.get(User, user_id)
            assert user is not None
            user.is_active = False
        else:
            tenant = db.get(Tenant, tenant_id)
            assert tenant is not None
            tenant.is_active = False
        db.commit()
    engine.dispose()

    response = _request(
        "GET",
        "/api/products/",
        migrated_postgresql_database_url,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.integration
def test_platform_account_does_not_depend_on_pharmacy_tenant_status(
    migrated_postgresql_database_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant = Tenant(
            name="Inactive Synthetic Platform Container",
            owner_email="platform-container@example.test",
            is_active=False,
        )
        db.add(tenant)
        db.flush()
        platform_user = User(
            email="platform-status@example.test",
            hashed_password=get_password_hash(PASSWORD),
            full_name="Synthetic Platform User",
            role="super_admin",
            is_active=True,
            tenant_id=tenant.id,
        )
        db.add(platform_user)
        db.commit()
    engine.dispose()

    response = _request(
        "POST",
        "/api/auth/login",
        migrated_postgresql_database_url,
        data={"username": "platform-status@example.test", "password": PASSWORD},
    )

    assert response.status_code == 200
