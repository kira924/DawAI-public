from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.security import normalize_email
from src.modules.auth import models, schemas
from src.modules.auth.principal import AuthenticatedPrincipal
from src.modules.tenants.models import Tenant
from src.modules.users import crud as user_crud
from src.modules.users.models import User


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get_owned_tenant(db: Session, actor: AuthenticatedPrincipal) -> Tenant:
    if actor.is_support_session or actor.role != "manager":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant owner required")
    tenant = db.query(Tenant).filter(Tenant.id == actor.tenant_id).with_for_update().first()
    if tenant is None or tenant.owner_user_id != actor.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant owner required")
    return tenant


def _record_audit(
    db: Session,
    grant: models.SupportAccessGrant,
    event_type: str,
    *,
    request_method: str | None = None,
    request_path: str | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        models.SupportAccessAudit(
            support_grant_id=grant.id,
            tenant_id=grant.tenant_id,
            support_user_id=grant.support_user_id,
            event_type=event_type,
            request_method=request_method,
            request_path=request_path,
            detail=detail,
            occurred_at=_utc_now(),
        )
    )


def create_support_grant(
    db: Session,
    actor: AuthenticatedPrincipal,
    grant_request: schemas.SupportGrantCreate,
) -> models.SupportAccessGrant:
    tenant = _get_owned_tenant(db, actor)
    support_user = user_crud.get_user_by_email(
        db,
        normalize_email(str(grant_request.support_user_email)),
    )
    if support_user is None or support_user.role != "super_admin" or not support_user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support user not found")
    if grant_request.duration_minutes > settings.SUPPORT_ACCESS_MAX_MINUTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Support grant duration exceeds the configured maximum",
        )

    now = _utc_now()
    grant = models.SupportAccessGrant(
        tenant_id=tenant.id,
        support_user_id=support_user.id,
        granted_by_user_id=actor.id,
        scope=grant_request.scope.value,
        reason=grant_request.reason.strip(),
        created_at=now,
        expires_at=now + timedelta(minutes=grant_request.duration_minutes),
    )
    db.add(grant)
    db.flush()
    _record_audit(db, grant, "grant_created", detail=f"scope={grant.scope}")
    db.commit()
    db.refresh(grant)
    return grant


def list_support_grants(
    db: Session,
    actor: AuthenticatedPrincipal,
    *,
    skip: int = 0,
    limit: int = 100,
) -> list[models.SupportAccessGrant]:
    tenant = _get_owned_tenant(db, actor)
    return (
        db.query(models.SupportAccessGrant)
        .filter(models.SupportAccessGrant.tenant_id == tenant.id)
        .order_by(models.SupportAccessGrant.created_at.desc(), models.SupportAccessGrant.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def revoke_support_grant(
    db: Session,
    actor: AuthenticatedPrincipal,
    grant_id: str,
) -> models.SupportAccessGrant:
    tenant = _get_owned_tenant(db, actor)
    grant = (
        db.query(models.SupportAccessGrant)
        .filter(
            models.SupportAccessGrant.id == grant_id,
            models.SupportAccessGrant.tenant_id == tenant.id,
        )
        .with_for_update()
        .first()
    )
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support grant not found")
    if grant.revoked_at is None:
        grant.revoked_at = _utc_now()
        grant.revoked_by_user_id = actor.id
        _record_audit(db, grant, "grant_revoked")
        db.commit()
        db.refresh(grant)
    return grant


def list_support_audits(
    db: Session,
    actor: AuthenticatedPrincipal,
    grant_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> list[models.SupportAccessAudit]:
    tenant = _get_owned_tenant(db, actor)
    grant = (
        db.query(models.SupportAccessGrant.id)
        .filter(
            models.SupportAccessGrant.id == grant_id,
            models.SupportAccessGrant.tenant_id == tenant.id,
        )
        .first()
    )
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Support grant not found")
    return (
        db.query(models.SupportAccessAudit)
        .filter(
            models.SupportAccessAudit.support_grant_id == grant_id,
            models.SupportAccessAudit.tenant_id == tenant.id,
        )
        .order_by(models.SupportAccessAudit.occurred_at.asc(), models.SupportAccessAudit.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def activate_support_context(
    db: Session,
    support_user: User,
    session_id: str,
    grant_id: str,
    request: Request,
) -> AuthenticatedPrincipal:
    now = _utc_now()
    grant = (
        db.query(models.SupportAccessGrant).filter(models.SupportAccessGrant.id == grant_id).first()
    )
    valid_grant = (
        grant is not None
        and support_user.role == "super_admin"
        and grant.support_user_id == support_user.id
        and grant.revoked_at is None
        and grant.expires_at > now
    )
    tenant = db.get(Tenant, grant.tenant_id) if grant is not None else None
    owner = db.get(User, grant.granted_by_user_id) if grant is not None else None
    valid_grant = bool(
        valid_grant
        and tenant is not None
        and tenant.is_active
        and tenant.owner_user_id == grant.granted_by_user_id
        and owner is not None
        and owner.is_active
        and owner.role == "manager"
        and owner.tenant_id == tenant.id
    )

    if not valid_grant or grant is None or tenant is None or owner is None:
        if grant is not None and grant.support_user_id == support_user.id:
            _record_audit(
                db,
                grant,
                "access_denied",
                request_method=request.method,
                request_path=request.url.path,
                detail="Grant is inactive or ownership is no longer valid",
            )
            db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid support grant")

    _record_audit(
        db,
        grant,
        "request_started",
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return AuthenticatedPrincipal(
        id=owner.id,
        auth_user_id=support_user.id,
        email=support_user.email,
        full_name=support_user.full_name,
        role="manager",
        tenant_id=tenant.id,
        session_id=session_id,
        support_grant_id=grant.id,
        support_scope=grant.scope,
    )
