from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from src.api.pagination import AuditPageLimit, PageOffset
from src.core.config import settings
from src.core.database import get_db
from src.modules.auth import schemas, service
from src.modules.auth import support as support_service
from src.modules.auth.dependencies import get_current_user
from src.modules.auth.notifications import (
    EmailDeliveryUnavailable,
    ensure_email_delivery_configured,
)
from src.modules.auth.principal import AuthenticatedPrincipal
from src.modules.users.models import User

router = APIRouter(tags=["Authentication"])


def _require_non_support_session(current_user: AuthenticatedPrincipal) -> None:
    if current_user.is_support_session:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Support sessions cannot manage the support user's account",
        )


def _email_delivery_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Account email delivery is temporarily unavailable",
    )


@router.post("/auth/login", response_model=schemas.Token)
def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else None
    service.ensure_login_not_throttled(db, form_data.username, client_ip)
    user = service.authenticate_user(db, email=form_data.username, password=form_data.password)
    if user is None:
        is_blocked = service.record_login_failure(db, form_data.username, client_ip)
        if is_blocked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again later.",
                headers={"Retry-After": str(settings.LOGIN_BLOCK_MINUTES * 60)},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    service.clear_account_throttle(db, form_data.username)
    return service.issue_token_pair(
        db,
        user,
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/auth/refresh", response_model=schemas.Token)
def refresh_access_token(
    token_request: schemas.RefreshTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    return service.rotate_refresh_token(
        db,
        token_request.refresh_token,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/auth/logout", response_model=schemas.StatusMessage)
def logout(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service.revoke_session(db, current_user.session_id, current_user.auth_user_id)
    return {"detail": "Session revoked"}


@router.post("/auth/logout-all", response_model=schemas.StatusMessage)
def logout_all(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service.revoke_all_sessions(db, current_user.auth_user_id)
    return {"detail": "All sessions revoked"}


@router.get("/account/me", response_model=schemas.SelfProfileResponse)
def read_own_profile(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_non_support_session(current_user)
    user = db.get(User, current_user.auth_user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/account/me", response_model=schemas.SelfProfileResponse)
def update_own_profile(
    profile_update: schemas.SelfProfileUpdate,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_non_support_session(current_user)
    return service.update_self_profile(db, current_user.auth_user_id, profile_update.full_name)


@router.post("/account/change-password", response_model=schemas.StatusMessage)
def change_own_password(
    password_change: schemas.PasswordChangeRequest,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_non_support_session(current_user)
    service.change_password(
        db,
        current_user.auth_user_id,
        password_change.current_password,
        password_change.new_password,
    )
    return {"detail": "Password changed; all sessions were revoked"}


@router.post(
    "/auth/password-reset/request",
    response_model=schemas.StatusMessage,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_password_reset(
    reset_request: schemas.PasswordResetRequest,
    db: Session = Depends(get_db),
):
    try:
        ensure_email_delivery_configured()
        service.request_password_reset(db, str(reset_request.email))
    except EmailDeliveryUnavailable:
        raise _email_delivery_unavailable()
    return {"detail": "If the account is eligible, a reset link has been sent"}


@router.post("/auth/password-reset/confirm", response_model=schemas.StatusMessage)
def confirm_password_reset(
    reset_confirmation: schemas.PasswordResetConfirm,
    db: Session = Depends(get_db),
):
    service.confirm_password_reset(
        db,
        reset_confirmation.token,
        reset_confirmation.new_password,
    )
    return {"detail": "Password reset completed; all sessions were revoked"}


@router.post("/account/email-change/request", response_model=schemas.StatusMessage)
def request_email_change(
    email_change: schemas.EmailChangeRequest,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_non_support_session(current_user)
    try:
        ensure_email_delivery_configured()
        service.request_email_change(
            db,
            current_user.auth_user_id,
            email_change.current_password,
            str(email_change.new_email),
        )
    except EmailDeliveryUnavailable:
        raise _email_delivery_unavailable()
    return {"detail": "A verification link has been sent to the new email"}


@router.post("/auth/email-change/confirm", response_model=schemas.StatusMessage)
def confirm_email_change(
    email_confirmation: schemas.EmailChangeConfirm,
    db: Session = Depends(get_db),
):
    service.confirm_email_change(db, email_confirmation.token)
    return {"detail": "Email changed; all sessions were revoked"}


@router.post(
    "/support/grants",
    response_model=schemas.SupportGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_support_grant(
    grant_request: schemas.SupportGrantCreate,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return support_service.create_support_grant(db, current_user, grant_request)


@router.get("/support/grants", response_model=list[schemas.SupportGrantResponse])
def list_support_grants(
    skip: PageOffset = 0,
    limit: AuditPageLimit = 100,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return support_service.list_support_grants(db, current_user, skip=skip, limit=limit)


@router.post(
    "/support/grants/{grant_id}/revoke",
    response_model=schemas.SupportGrantResponse,
)
def revoke_support_grant(
    grant_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return support_service.revoke_support_grant(db, current_user, grant_id)


@router.get(
    "/support/grants/{grant_id}/audit",
    response_model=list[schemas.SupportAuditResponse],
)
def list_support_grant_audits(
    grant_id: str,
    skip: PageOffset = 0,
    limit: AuditPageLimit = 100,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return support_service.list_support_audits(
        db,
        current_user,
        grant_id,
        skip=skip,
        limit=limit,
    )
