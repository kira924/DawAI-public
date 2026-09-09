import asyncio
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.core.security import get_password_hash
from src.main import app
from src.modules.auth.authorization import ROLE_PERMISSIONS, Permission
from src.modules.inventory.models import Product
from src.modules.tenants.models import Tenant, TenantOwnershipTransfer
from src.modules.users.models import User

PASSWORD = "Synthetic-RBAC-password-123"


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


def _seed_rbac_accounts(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    password_hash = get_password_hash(PASSWORD)

    with Session(engine) as db:
        tenant_a = Tenant(
            name="Synthetic Pharmacy A",
            owner_email="owner-a@example.com",
            is_active=False,
        )
        tenant_b = Tenant(
            name="Synthetic Pharmacy B",
            owner_email="owner-b@example.com",
            is_active=False,
        )
        db.add_all([tenant_a, tenant_b])
        db.flush()

        accounts = {
            "super_admin": User(
                email="super-admin@example.com",
                hashed_password=password_hash,
                full_name="Synthetic Platform Administrator",
                role="super_admin",
                tenant_id=tenant_a.id,
            ),
            "owner_manager": User(
                email="owner-manager@example.com",
                hashed_password=password_hash,
                full_name="Synthetic Owner Manager",
                role="manager",
                tenant_id=tenant_a.id,
            ),
            "manager": User(
                email="manager@example.com",
                hashed_password=password_hash,
                full_name="Synthetic Manager",
                role="manager",
                tenant_id=tenant_a.id,
            ),
            "peer_manager": User(
                email="peer-manager@example.com",
                hashed_password=password_hash,
                full_name="Synthetic Peer Manager",
                role="manager",
                tenant_id=tenant_a.id,
            ),
            "pharmacist": User(
                email="pharmacist-rbac@example.com",
                hashed_password=password_hash,
                full_name="Synthetic Pharmacist",
                role="pharmacist",
                tenant_id=tenant_a.id,
            ),
            "foreign_pharmacist": User(
                email="foreign-pharmacist@example.com",
                hashed_password=password_hash,
                full_name="Synthetic Foreign Pharmacist",
                role="pharmacist",
                tenant_id=tenant_b.id,
            ),
            "foreign_owner": User(
                email="foreign-owner@example.com",
                hashed_password=password_hash,
                full_name="Synthetic Foreign Owner",
                role="manager",
                tenant_id=tenant_b.id,
            ),
        }
        db.add_all(accounts.values())
        db.flush()
        tenant_a.owner_user_id = accounts["owner_manager"].id
        tenant_a.owner_email = accounts["owner_manager"].email
        tenant_a.is_active = True
        tenant_b.owner_user_id = accounts["foreign_owner"].id
        tenant_b.owner_email = accounts["foreign_owner"].email
        tenant_b.is_active = True
        db.commit()

        identifiers = {name: account.id for name, account in accounts.items()}
        identifiers["tenant_a"] = tenant_a.id
        identifiers["tenant_b"] = tenant_b.id

    engine.dispose()
    return identifiers


def _authorization_header(database_url: str, email: str) -> dict[str, str]:
    response = _request(
        "POST",
        "/api/auth/login",
        database_url,
        form_data={"username": email, "password": PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_permission_map_matches_confirmed_policy() -> None:
    assert ROLE_PERMISSIONS == {
        "super_admin": frozenset(
            {
                Permission.PLATFORM_ADMIN,
                Permission.TENANT_SETTINGS,
                Permission.USER_ADMIN,
            }
        ),
        "manager": frozenset(
            {
                Permission.TENANT_SETTINGS,
                Permission.USER_ADMIN,
                Permission.OPERATIONS,
                Permission.REPORTS,
                Permission.INVENTORY_CONTROL,
            }
        ),
        "pharmacist": frozenset({Permission.OPERATIONS}),
    }


@pytest.mark.integration
def test_financial_ledger_requires_reports_access(
    migrated_postgresql_database_url: str,
) -> None:
    _seed_rbac_accounts(migrated_postgresql_database_url)
    pharmacist_headers = _authorization_header(
        migrated_postgresql_database_url,
        "pharmacist-rbac@example.com",
    )
    manager_headers = _authorization_header(
        migrated_postgresql_database_url,
        "manager@example.com",
    )

    pharmacist_history = _request(
        "GET",
        "/api/finance/transactions",
        migrated_postgresql_database_url,
        headers=pharmacist_headers,
    )
    manager_history = _request(
        "GET",
        "/api/finance/transactions",
        migrated_postgresql_database_url,
        headers=manager_headers,
    )
    manager_reconciliation = _request(
        "GET",
        "/api/finance/reconciliation",
        migrated_postgresql_database_url,
        headers=manager_headers,
    )

    assert pharmacist_history.status_code == 403
    assert manager_history.status_code == 200
    assert manager_history.json() == []
    assert manager_reconciliation.status_code == 200
    assert manager_reconciliation.json()["issue_count"] == 0


@pytest.mark.integration
def test_stock_history_is_operational_but_reconciliation_requires_reports_access(
    migrated_postgresql_database_url: str,
) -> None:
    _seed_rbac_accounts(migrated_postgresql_database_url)
    pharmacist_headers = _authorization_header(
        migrated_postgresql_database_url,
        "pharmacist-rbac@example.com",
    )
    manager_headers = _authorization_header(
        migrated_postgresql_database_url,
        "manager@example.com",
    )

    pharmacist_history = _request(
        "GET",
        "/api/products/stock-movements",
        migrated_postgresql_database_url,
        headers=pharmacist_headers,
    )
    pharmacist_reconciliation = _request(
        "GET",
        "/api/products/stock-reconciliation",
        migrated_postgresql_database_url,
        headers=pharmacist_headers,
    )
    manager_reconciliation = _request(
        "GET",
        "/api/products/stock-reconciliation",
        migrated_postgresql_database_url,
        headers=manager_headers,
    )

    assert pharmacist_history.status_code == 200
    assert pharmacist_history.json() == []
    assert pharmacist_reconciliation.status_code == 403
    assert manager_reconciliation.status_code == 200
    assert manager_reconciliation.json() == {
        "checked_products": 0,
        "checked_batches": 0,
        "issue_count": 0,
        "issues_truncated": False,
        "issues": [],
    }

    pharmacist_apply = _request(
        "POST",
        "/api/products/stock-counts/999/apply",
        migrated_postgresql_database_url,
        headers=pharmacist_headers,
    )
    manager_apply = _request(
        "POST",
        "/api/products/stock-counts/999/apply",
        migrated_postgresql_database_url,
        headers=manager_headers,
    )
    pharmacist_disposition = _request(
        "POST",
        "/api/products/quarantine/999/dispositions",
        migrated_postgresql_database_url,
        json_body={
            "action": "dispose",
            "quantity": 1,
            "reason": "Synthetic permission probe",
        },
        headers=pharmacist_headers,
    )
    manager_disposition = _request(
        "POST",
        "/api/products/quarantine/999/dispositions",
        migrated_postgresql_database_url,
        json_body={
            "action": "dispose",
            "quantity": 1,
            "reason": "Synthetic permission probe",
        },
        headers=manager_headers,
    )
    assert pharmacist_apply.status_code == 403
    assert manager_apply.status_code == 404
    assert pharmacist_disposition.status_code == 403
    assert manager_disposition.status_code == 404


@pytest.mark.integration
def test_super_admin_has_platform_access_without_operational_access(
    migrated_postgresql_database_url: str,
) -> None:
    _seed_rbac_accounts(migrated_postgresql_database_url)
    headers = _authorization_header(
        migrated_postgresql_database_url,
        "super-admin@example.com",
    )

    assert (
        _request(
            "GET", "/api/tenants/", migrated_postgresql_database_url, headers=headers
        ).status_code
        == 200
    )
    assert (
        _request(
            "GET", "/api/users/", migrated_postgresql_database_url, headers=headers
        ).status_code
        == 200
    )

    operational_paths = [
        "/api/products/",
        "/api/sales/",
        "/api/purchases/suppliers/",
        "/api/customers/",
        "/api/expenses/",
        "/api/shifts/active",
        "/api/reports/dashboard",
    ]
    for path in operational_paths:
        response = _request("GET", path, migrated_postgresql_database_url, headers=headers)
        assert response.status_code == 403, path
        assert response.json() == {"detail": "Insufficient permissions"}


@pytest.mark.integration
def test_manager_has_tenant_operations_reports_and_limited_settings(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_rbac_accounts(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url, "manager@example.com")

    allowed_paths = [
        "/api/products/",
        "/api/sales/",
        "/api/purchases/suppliers/",
        "/api/customers/",
        "/api/expenses/",
        "/api/reports/dashboard",
        f"/api/tenants/{identifiers['tenant_a']}",
    ]
    for path in allowed_paths:
        response = _request("GET", path, migrated_postgresql_database_url, headers=headers)
        assert response.status_code == 200, path

    tenant_list = _request(
        "GET", "/api/tenants/", migrated_postgresql_database_url, headers=headers
    )
    assert tenant_list.status_code == 403

    foreign_tenant = _request(
        "GET",
        f"/api/tenants/{identifiers['tenant_b']}",
        migrated_postgresql_database_url,
        headers=headers,
    )
    assert foreign_tenant.status_code == 404

    rename = _request(
        "PATCH",
        f"/api/tenants/{identifiers['tenant_a']}",
        migrated_postgresql_database_url,
        json_body={"name": "Renamed Synthetic Pharmacy"},
        headers=headers,
    )
    assert rename.status_code == 200

    status_change = _request(
        "PATCH",
        f"/api/tenants/{identifiers['tenant_a']}",
        migrated_postgresql_database_url,
        json_body={"is_active": False},
        headers=headers,
    )
    assert status_change.status_code == 403


@pytest.mark.integration
def test_pharmacist_has_operations_without_administrative_access(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_rbac_accounts(migrated_postgresql_database_url)
    headers = _authorization_header(
        migrated_postgresql_database_url,
        "pharmacist-rbac@example.com",
    )

    allowed_paths = [
        "/api/products/",
        "/api/sales/",
        "/api/purchases/suppliers/",
        "/api/customers/",
        "/api/expenses/",
    ]
    for path in allowed_paths:
        response = _request("GET", path, migrated_postgresql_database_url, headers=headers)
        assert response.status_code == 200, path

    denied_paths = [
        "/api/reports/dashboard",
        "/api/users/",
        f"/api/tenants/{identifiers['tenant_a']}",
    ]
    for path in denied_paths:
        response = _request("GET", path, migrated_postgresql_database_url, headers=headers)
        assert response.status_code == 403, path
        assert response.json() == {"detail": "Insufficient permissions"}


@pytest.mark.integration
def test_replenishment_policy_is_manager_controlled_but_operationally_visible(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_rbac_accounts(migrated_postgresql_database_url)
    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        product = Product(
            name="Synthetic Replenishment Product",
            price=10,
            total_parts=0,
            tenant_id=identifiers["tenant_a"],
        )
        foreign_product = Product(
            name="Foreign Replenishment Product",
            price=10,
            total_parts=0,
            tenant_id=identifiers["tenant_b"],
        )
        db.add_all([product, foreign_product])
        db.commit()
        product_id = product.id
        foreign_product_id = foreign_product.id
    engine.dispose()

    manager_headers = _authorization_header(migrated_postgresql_database_url, "manager@example.com")
    pharmacist_headers = _authorization_header(
        migrated_postgresql_database_url, "pharmacist-rbac@example.com"
    )
    payload = {"reorder_point_parts": 2, "target_stock_parts": 10}

    denied = _request(
        "PUT",
        f"/api/products/{product_id}/replenishment-policy",
        migrated_postgresql_database_url,
        json_body=payload,
        headers=pharmacist_headers,
    )
    assert denied.status_code == 403

    configured = _request(
        "PUT",
        f"/api/products/{product_id}/replenishment-policy",
        migrated_postgresql_database_url,
        json_body=payload,
        headers=manager_headers,
    )
    assert configured.status_code == 200
    assert configured.json()["is_configured"] is True

    foreign = _request(
        "PUT",
        f"/api/products/{foreign_product_id}/replenishment-policy",
        migrated_postgresql_database_url,
        json_body=payload,
        headers=manager_headers,
    )
    assert foreign.status_code == 404

    visible = _request(
        "GET",
        "/api/products/replenishment",
        migrated_postgresql_database_url,
        headers=pharmacist_headers,
    )
    assert visible.status_code == 200
    assert [item["product_id"] for item in visible.json()] == [product_id]


@pytest.mark.integration
def test_manager_can_administer_only_same_tenant_pharmacists(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_rbac_accounts(migrated_postgresql_database_url)
    headers = _authorization_header(migrated_postgresql_database_url, "manager@example.com")

    users_response = _request(
        "GET",
        "/api/users/",
        migrated_postgresql_database_url,
        headers=headers,
    )
    assert users_response.status_code == 200
    visible_ids = {user["id"] for user in users_response.json()}
    assert visible_ids == {identifiers["manager"], identifiers["pharmacist"]}

    for visible_user_id in (identifiers["manager"], identifiers["pharmacist"]):
        response = _request(
            "GET",
            f"/api/users/{visible_user_id}",
            migrated_postgresql_database_url,
            headers=headers,
        )
        assert response.status_code == 200

    for hidden_user_id in (identifiers["peer_manager"], identifiers["foreign_pharmacist"]):
        response = _request(
            "GET",
            f"/api/users/{hidden_user_id}",
            migrated_postgresql_database_url,
            headers=headers,
        )
        assert response.status_code == 404

    create_pharmacist = _request(
        "POST",
        "/api/users/",
        migrated_postgresql_database_url,
        json_body={
            "email": "new-pharmacist@example.com",
            "full_name": "New Synthetic Pharmacist",
            "role": "pharmacist",
            "tenant_id": identifiers["tenant_a"],
            "password": PASSWORD,
        },
        headers=headers,
    )
    assert create_pharmacist.status_code == 201

    forbidden_creations = [
        {
            "email": "new-manager@example.com",
            "full_name": "New Synthetic Manager",
            "role": "manager",
            "tenant_id": identifiers["tenant_a"],
            "password": PASSWORD,
        },
        {
            "email": "foreign-new-pharmacist@example.com",
            "full_name": "Foreign Synthetic Pharmacist",
            "role": "pharmacist",
            "tenant_id": identifiers["tenant_b"],
            "password": PASSWORD,
        },
    ]
    for payload in forbidden_creations:
        response = _request(
            "POST",
            "/api/users/",
            migrated_postgresql_database_url,
            json_body=payload,
            headers=headers,
        )
        assert response.status_code == 403

    update_pharmacist = _request(
        "PATCH",
        f"/api/users/{identifiers['pharmacist']}",
        migrated_postgresql_database_url,
        json_body={"full_name": "Updated Synthetic Pharmacist"},
        headers=headers,
    )
    assert update_pharmacist.status_code == 200

    promote_pharmacist = _request(
        "PATCH",
        f"/api/users/{identifiers['pharmacist']}",
        migrated_postgresql_database_url,
        json_body={"role": "manager"},
        headers=headers,
    )
    assert promote_pharmacist.status_code == 403

    update_peer_manager = _request(
        "PATCH",
        f"/api/users/{identifiers['peer_manager']}",
        migrated_postgresql_database_url,
        json_body={"full_name": "Unauthorized Update"},
        headers=headers,
    )
    assert update_peer_manager.status_code == 404

    assign_password = _request(
        "PATCH",
        f"/api/users/{identifiers['pharmacist']}",
        migrated_postgresql_database_url,
        json_body={"password": "Administrator-assigned-password"},
        headers=headers,
    )
    assert assign_password.status_code == 422


@pytest.mark.integration
def test_tenant_owner_can_administer_same_tenant_managers(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_rbac_accounts(migrated_postgresql_database_url)
    headers = _authorization_header(
        migrated_postgresql_database_url,
        "owner-manager@example.com",
    )

    users_response = _request(
        "GET",
        "/api/users/",
        migrated_postgresql_database_url,
        headers=headers,
    )
    assert users_response.status_code == 200
    visible_ids = {user["id"] for user in users_response.json()}
    assert visible_ids == {
        identifiers["owner_manager"],
        identifiers["manager"],
        identifiers["peer_manager"],
        identifiers["pharmacist"],
    }

    create_manager = _request(
        "POST",
        "/api/users/",
        migrated_postgresql_database_url,
        json_body={
            "email": "owner-created-manager@example.com",
            "full_name": "Owner Created Manager",
            "role": "manager",
            "tenant_id": identifiers["tenant_a"],
            "password": PASSWORD,
        },
        headers=headers,
    )
    assert create_manager.status_code == 201

    update_manager = _request(
        "PATCH",
        f"/api/users/{identifiers['peer_manager']}",
        migrated_postgresql_database_url,
        json_body={"full_name": "Owner Updated Manager"},
        headers=headers,
    )
    assert update_manager.status_code == 200

    promote_to_platform = _request(
        "PATCH",
        f"/api/users/{identifiers['peer_manager']}",
        migrated_postgresql_database_url,
        json_body={"role": "super_admin"},
        headers=headers,
    )
    assert promote_to_platform.status_code == 403


@pytest.mark.integration
def test_owner_transfer_is_atomic_audited_and_changes_manager_boundaries(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_rbac_accounts(migrated_postgresql_database_url)
    owner_headers = _authorization_header(
        migrated_postgresql_database_url,
        "owner-manager@example.com",
    )

    transfer = _request(
        "PUT",
        f"/api/tenants/{identifiers['tenant_a']}/owner",
        migrated_postgresql_database_url,
        json_body={"target_user_id": identifiers["peer_manager"]},
        headers=owner_headers,
    )
    assert transfer.status_code == 200
    assert transfer.json()["previous_owner_user_id"] == identifiers["owner_manager"]
    assert transfer.json()["new_owner_user_id"] == identifiers["peer_manager"]
    assert transfer.json()["performed_by_user_id"] == identifiers["owner_manager"]

    old_owner_users = _request(
        "GET",
        "/api/users/",
        migrated_postgresql_database_url,
        headers=owner_headers,
    )
    assert old_owner_users.status_code == 200
    assert {user["id"] for user in old_owner_users.json()} == {
        identifiers["owner_manager"],
        identifiers["pharmacist"],
    }

    new_owner_headers = _authorization_header(
        migrated_postgresql_database_url,
        "peer-manager@example.com",
    )
    new_owner_users = _request(
        "GET",
        "/api/users/",
        migrated_postgresql_database_url,
        headers=new_owner_headers,
    )
    assert new_owner_users.status_code == 200
    assert identifiers["manager"] in {user["id"] for user in new_owner_users.json()}

    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant = db.get(Tenant, identifiers["tenant_a"])
        assert tenant is not None
        assert tenant.owner_user_id == identifiers["peer_manager"]
        assert tenant.owner_email == "peer-manager@example.com"
        assert db.query(TenantOwnershipTransfer).count() == 1
    engine.dispose()


@pytest.mark.integration
def test_ownership_rejects_invalid_targets_and_protects_current_owner(
    migrated_postgresql_database_url: str,
) -> None:
    identifiers = _seed_rbac_accounts(migrated_postgresql_database_url)
    owner_headers = _authorization_header(
        migrated_postgresql_database_url,
        "owner-manager@example.com",
    )
    ordinary_manager_headers = _authorization_header(
        migrated_postgresql_database_url,
        "manager@example.com",
    )
    platform_headers = _authorization_header(
        migrated_postgresql_database_url,
        "super-admin@example.com",
    )

    invalid_targets = [
        (identifiers["pharmacist"], 409),
        (identifiers["foreign_owner"], 404),
    ]
    for target_user_id, expected_status in invalid_targets:
        response = _request(
            "PUT",
            f"/api/tenants/{identifiers['tenant_a']}/owner",
            migrated_postgresql_database_url,
            json_body={"target_user_id": target_user_id},
            headers=owner_headers,
        )
        assert response.status_code == expected_status

    ordinary_transfer = _request(
        "PUT",
        f"/api/tenants/{identifiers['tenant_a']}/owner",
        migrated_postgresql_database_url,
        json_body={"target_user_id": identifiers["peer_manager"]},
        headers=ordinary_manager_headers,
    )
    assert ordinary_transfer.status_code == 403

    deactivate_owner = _request(
        "PATCH",
        f"/api/users/{identifiers['owner_manager']}",
        migrated_postgresql_database_url,
        json_body={"is_active": False},
        headers=platform_headers,
    )
    assert deactivate_owner.status_code == 409

    demote_owner = _request(
        "PATCH",
        f"/api/users/{identifiers['owner_manager']}",
        migrated_postgresql_database_url,
        json_body={"role": "pharmacist"},
        headers=platform_headers,
    )
    assert demote_owner.status_code == 409

    engine = create_engine(migrated_postgresql_database_url)
    with Session(engine) as db:
        tenant = db.get(Tenant, identifiers["tenant_a"])
        assert tenant is not None
        assert tenant.owner_user_id == identifiers["owner_manager"]
        assert db.query(TenantOwnershipTransfer).count() == 0
    engine.dispose()

    support_transfer = _request(
        "PUT",
        f"/api/tenants/{identifiers['tenant_a']}/owner",
        migrated_postgresql_database_url,
        json_body={"target_user_id": identifiers["peer_manager"]},
        headers=platform_headers,
    )
    assert support_transfer.status_code == 200
    assert support_transfer.json()["performed_by_user_id"] == identifiers["super_admin"]


@pytest.mark.integration
def test_platform_onboarding_requires_owner_before_tenant_activation(
    migrated_postgresql_database_url: str,
) -> None:
    _seed_rbac_accounts(migrated_postgresql_database_url)
    platform_headers = _authorization_header(
        migrated_postgresql_database_url,
        "super-admin@example.com",
    )

    tenant_response = _request(
        "POST",
        "/api/tenants/",
        migrated_postgresql_database_url,
        json_body={
            "name": "New Synthetic Pharmacy",
            "owner_email": "new-tenant-owner@example.com",
        },
        headers=platform_headers,
    )
    assert tenant_response.status_code == 201
    tenant = tenant_response.json()
    assert tenant["is_active"] is False
    assert tenant["owner_user_id"] is None

    manager_response = _request(
        "POST",
        "/api/users/",
        migrated_postgresql_database_url,
        json_body={
            "email": "new-tenant-owner@example.com",
            "full_name": "New Tenant Owner",
            "role": "manager",
            "tenant_id": tenant["id"],
            "password": PASSWORD,
        },
        headers=platform_headers,
    )
    assert manager_response.status_code == 201

    premature_activation = _request(
        "PATCH",
        f"/api/tenants/{tenant['id']}",
        migrated_postgresql_database_url,
        json_body={"is_active": True},
        headers=platform_headers,
    )
    assert premature_activation.status_code == 409

    ownership_response = _request(
        "PUT",
        f"/api/tenants/{tenant['id']}/owner",
        migrated_postgresql_database_url,
        json_body={"target_user_id": manager_response.json()["id"]},
        headers=platform_headers,
    )
    assert ownership_response.status_code == 200

    activation_response = _request(
        "PATCH",
        f"/api/tenants/{tenant['id']}",
        migrated_postgresql_database_url,
        json_body={"is_active": True},
        headers=platform_headers,
    )
    assert activation_response.status_code == 200
    assert activation_response.json()["is_active"] is True
