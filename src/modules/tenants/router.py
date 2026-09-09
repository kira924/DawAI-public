from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.api.pagination import PageLimit, PageOffset
from src.core.database import get_db
from src.modules.auth.authorization import (
    require_platform_administration,
    require_tenant_settings_access,
)
from src.modules.tenants import crud, schemas, service
from src.modules.users.models import User

router = APIRouter(prefix="/tenants", tags=["Tenants (Pharmacies)"])


# 1. Register a new tenant (Super Admin Only)
@router.post("/", response_model=schemas.TenantResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    tenant: schemas.TenantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_platform_administration),
):
    return crud.create_tenant(db=db, tenant=tenant)


# 2. Read all tenants (Super Admin Only)
@router.get("/", response_model=List[schemas.TenantResponse])
def read_tenants(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_platform_administration),
):
    return crud.get_tenants(db, skip=skip, limit=limit)


# 3. Read a specific tenant by ID (Super Admin OR Manager of the same tenant)
@router.get("/{tenant_id}", response_model=schemas.TenantResponse)
def read_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_settings_access),
):
    if current_user.role != "super_admin" and current_user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    db_tenant = crud.get_tenant(db, tenant_id=tenant_id)
    if db_tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    return db_tenant


# Update a specific tenant by ID (Super Admin OR Manager of the same tenant)
@router.patch("/{tenant_id}", response_model=schemas.TenantResponse)
def update_tenant(
    tenant_id: int,
    tenant_update: schemas.TenantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_settings_access),
):
    if current_user.role != "super_admin" and current_user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    platform_fields = {"subscription_plan", "is_active"}
    if current_user.role != "super_admin" and tenant_update.model_fields_set & platform_fields:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can update subscription or tenant status",
        )

    return service.update_tenant(db=db, tenant_id=tenant_id, tenant_update=tenant_update)


@router.put(
    "/{tenant_id}/owner",
    response_model=schemas.TenantOwnershipTransferResponse,
)
def assign_or_transfer_tenant_ownership(
    tenant_id: int,
    ownership_update: schemas.TenantOwnershipUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_settings_access),
):
    return service.assign_or_transfer_ownership(
        db=db,
        tenant_id=tenant_id,
        target_user_id=ownership_update.target_user_id,
        actor=current_user,
    )
