from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.modules.tenants import models, schemas
from src.modules.users.models import User
from src.modules.users.schemas import UserRole


def _get_locked_tenant(db: Session, tenant_id: int) -> models.Tenant:
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).with_for_update().first()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def _get_eligible_owner(db: Session, tenant_id: int, user_id: int) -> User:
    target = (
        db.query(User)
        .filter(User.id == user_id, User.tenant_id == tenant_id)
        .with_for_update()
        .first()
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eligible owner not found",
        )
    if target.role != UserRole.MANAGER.value or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenant owner must be an active manager in the same tenant",
        )
    return target


def update_tenant(
    db: Session,
    tenant_id: int,
    tenant_update: schemas.TenantUpdate,
) -> models.Tenant:
    tenant = _get_locked_tenant(db, tenant_id)
    update_data = tenant_update.model_dump(exclude_unset=True)

    if update_data.get("is_active") is True:
        if tenant.owner_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Assign an active manager as tenant owner before activation",
            )
        _get_eligible_owner(db, tenant.id, tenant.owner_user_id)

    for key, value in update_data.items():
        setattr(tenant, key, value)

    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenant update violates an ownership constraint",
        ) from error

    db.refresh(tenant)
    return tenant


def assign_or_transfer_ownership(
    db: Session,
    tenant_id: int,
    target_user_id: int,
    actor: User,
) -> models.TenantOwnershipTransfer:
    tenant = _get_locked_tenant(db, tenant_id)

    if actor.role != UserRole.SUPER_ADMIN.value:
        if actor.tenant_id != tenant.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        if tenant.owner_user_id != actor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the tenant owner can transfer ownership",
            )

    target = _get_eligible_owner(db, tenant.id, target_user_id)
    if tenant.owner_user_id == target.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already the tenant owner",
        )

    transfer = models.TenantOwnershipTransfer(
        tenant_id=tenant.id,
        previous_owner_user_id=tenant.owner_user_id,
        new_owner_user_id=target.id,
        performed_by_user_id=actor.id,
    )
    tenant.owner_user_id = target.id
    tenant.owner_email = target.email
    db.add(transfer)

    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ownership transfer violates a tenant constraint",
        ) from error

    db.refresh(transfer)
    return transfer
