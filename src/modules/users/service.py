from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from src.modules.tenants.models import Tenant
from src.modules.users import models, schemas


def ensure_can_create_user(
    db: Session,
    actor: models.User,
    requested_user: schemas.UserCreate,
) -> None:
    tenant = (
        db.query(Tenant).filter(Tenant.id == requested_user.tenant_id).with_for_update().first()
    )
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if actor.role == schemas.UserRole.SUPER_ADMIN.value:
        return
    if actor.role == schemas.UserRole.MANAGER.value and tenant.id == actor.tenant_id:
        if requested_user.role == schemas.UserRole.PHARMACIST:
            return
        if tenant.owner_user_id == actor.id and requested_user.role == schemas.UserRole.MANAGER:
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to create this user",
    )


def visible_users_query(db: Session, actor: models.User) -> Query:
    query = db.query(models.User)
    if actor.role == schemas.UserRole.SUPER_ADMIN.value:
        return query
    if actor.role != schemas.UserRole.MANAGER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view users",
        )
    owner_user_id = db.query(Tenant.owner_user_id).filter(Tenant.id == actor.tenant_id).scalar()
    if owner_user_id == actor.id:
        return query.filter(
            models.User.tenant_id == actor.tenant_id,
            models.User.role.in_(
                [schemas.UserRole.MANAGER.value, schemas.UserRole.PHARMACIST.value]
            ),
        )
    return query.filter(
        models.User.tenant_id == actor.tenant_id,
        or_(models.User.id == actor.id, models.User.role == schemas.UserRole.PHARMACIST.value),
    )


def get_visible_user(db: Session, actor: models.User, user_id: int) -> models.User | None:
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None:
        return None
    if actor.role == schemas.UserRole.SUPER_ADMIN.value:
        return target
    if actor.role != schemas.UserRole.MANAGER.value:
        return None
    if target.tenant_id != actor.tenant_id:
        return None
    owner_user_id = db.query(Tenant.owner_user_id).filter(Tenant.id == actor.tenant_id).scalar()
    if owner_user_id == actor.id and target.role != schemas.UserRole.SUPER_ADMIN.value:
        return target
    if target.id == actor.id or target.role == schemas.UserRole.PHARMACIST.value:
        return target
    return None


def get_administrable_user(db: Session, actor: models.User, user_id: int) -> models.User | None:
    target_identity = (
        db.query(models.User.id, models.User.tenant_id).filter(models.User.id == user_id).first()
    )
    if target_identity is None:
        return None

    tenant = (
        db.query(Tenant).filter(Tenant.id == target_identity.tenant_id).with_for_update().first()
    )
    target = (
        db.query(models.User)
        .filter(
            models.User.id == target_identity.id,
            models.User.tenant_id == target_identity.tenant_id,
        )
        .with_for_update()
        .first()
    )
    if tenant is None or target is None:
        return None
    if actor.role == schemas.UserRole.SUPER_ADMIN.value:
        return target
    if actor.role != schemas.UserRole.MANAGER.value or target.tenant_id != actor.tenant_id:
        return None
    if tenant.owner_user_id == actor.id:
        if target.id != actor.id and target.role != schemas.UserRole.SUPER_ADMIN.value:
            return target
        return None
    if target.role == schemas.UserRole.PHARMACIST.value:
        return target
    return None


def ensure_update_is_allowed(
    db: Session,
    actor: models.User,
    target: models.User,
    user_update: schemas.UserUpdate,
) -> None:
    tenant = db.query(Tenant).filter(Tenant.id == target.tenant_id).with_for_update().first()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if target.id == tenant.owner_user_id and (
        user_update.is_active is False
        or (user_update.role is not None and user_update.role != schemas.UserRole.MANAGER)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transfer tenant ownership before deactivating or demoting the owner",
        )

    if actor.role == schemas.UserRole.SUPER_ADMIN.value:
        return
    if actor.role != schemas.UserRole.MANAGER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update users",
        )
    if tenant.owner_user_id == actor.id:
        if user_update.role == schemas.UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to assign this role",
            )
        return
    if user_update.role not in {None, schemas.UserRole.PHARMACIST}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to assign this role",
        )
