from collections.abc import Callable, Mapping
from enum import Enum
from types import MappingProxyType
from typing import Final

from fastapi import Depends, HTTPException, Request, status

from src.modules.auth.dependencies import get_current_user
from src.modules.auth.principal import AuthenticatedPrincipal
from src.modules.users.schemas import UserRole


class Permission(str, Enum):
    PLATFORM_ADMIN = "platform_admin"
    TENANT_SETTINGS = "tenant_settings"
    USER_ADMIN = "user_admin"
    OPERATIONS = "operations"
    REPORTS = "reports"
    INVENTORY_CONTROL = "inventory_control"


ROLE_PERMISSIONS: Final[Mapping[str, frozenset[Permission]]] = MappingProxyType(
    {
        UserRole.SUPER_ADMIN.value: frozenset(
            {
                Permission.PLATFORM_ADMIN,
                Permission.TENANT_SETTINGS,
                Permission.USER_ADMIN,
            }
        ),
        UserRole.MANAGER.value: frozenset(
            {
                Permission.TENANT_SETTINGS,
                Permission.USER_ADMIN,
                Permission.OPERATIONS,
                Permission.REPORTS,
                Permission.INVENTORY_CONTROL,
            }
        ),
        UserRole.PHARMACIST.value: frozenset({Permission.OPERATIONS}),
    }
)


def has_permission(user: AuthenticatedPrincipal, permission: Permission) -> bool:
    if getattr(user, "is_support_session", False):
        return permission in {Permission.OPERATIONS, Permission.REPORTS}
    return permission in ROLE_PERMISSIONS.get(user.role, frozenset())


def require_permission(permission: Permission) -> Callable[..., AuthenticatedPrincipal]:
    def permission_dependency(
        request: Request,
        current_user: AuthenticatedPrincipal = Depends(get_current_user),
    ) -> AuthenticatedPrincipal:
        if not has_permission(current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        if (
            current_user.is_support_session
            and current_user.support_scope == "read_only"
            and request.method not in {"GET", "HEAD", "OPTIONS"}
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Read-only support grant cannot modify tenant data",
            )
        return current_user

    return permission_dependency


require_platform_administration = require_permission(Permission.PLATFORM_ADMIN)
require_tenant_settings_access = require_permission(Permission.TENANT_SETTINGS)
require_user_administration = require_permission(Permission.USER_ADMIN)
require_operations_access = require_permission(Permission.OPERATIONS)
require_reports_access = require_permission(Permission.REPORTS)
require_inventory_control = require_permission(Permission.INVENTORY_CONTROL)
