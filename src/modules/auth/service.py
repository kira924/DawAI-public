from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.security import (
    create_access_token,
    generate_opaque_token,
    get_password_hash,
    hash_opaque_token,
    hash_sensitive_identifier,
    normalize_email,
    verify_password,
)
from src.modules.auth import models, schemas
from src.modules.auth.notifications import send_account_email
from src.modules.users import crud as user_crud
from src.modules.users.models import User
from src.modules.users.schemas import UserRole

_DUMMY_PASSWORD_HASH = get_password_hash("DawAI-dummy-password-for-timing-only")


def is_account_enabled(user: User) -> bool:
    if not user.is_active:
        return False
    if user.role == UserRole.SUPER_ADMIN.value:
        return True
    return bool(user.tenant is not None and user.tenant.is_active)


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = user_crud.get_user_by_email(db, email=normalize_email(email))
    password_hash = user.hashed_password if user is not None else _DUMMY_PASSWORD_HASH
    password_matches = verify_password(password, password_hash)
    if user is None or not password_matches:
        return None
    if not is_account_enabled(user):
        return None
    return user


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _client_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hash_sensitive_identifier(value[:1024])


def _access_token_for(user: User, session_id: str) -> str:
    return create_access_token(
        {
            "sub": str(user.id),
            "sid": session_id,
            "tenant_id": user.tenant_id,
            "role": user.role,
        }
    )


def issue_token_pair(
    db: Session,
    user: User,
    *,
    client_ip: str | None,
    user_agent: str | None,
    family_id: str | None = None,
) -> schemas.Token:
    now = _utc_now()
    refresh_token = generate_opaque_token()
    session = models.AuthSession(
        id=str(uuid4()),
        family_id=family_id or str(uuid4()),
        refresh_token_hash=hash_opaque_token(refresh_token),
        user_id=user.id,
        tenant_id=user.tenant_id,
        created_at=now,
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        client_ip_hash=_client_hash(client_ip),
        user_agent_hash=_client_hash(user_agent),
    )
    db.add(session)
    db.commit()
    return schemas.Token(
        access_token=_access_token_for(user, session.id),
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def rotate_refresh_token(
    db: Session,
    refresh_token: str,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> schemas.Token:
    now = _utc_now()
    token_hash = hash_opaque_token(refresh_token)
    current_session = (
        db.query(models.AuthSession)
        .filter(models.AuthSession.refresh_token_hash == token_hash)
        .with_for_update()
        .first()
    )
    if current_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    if current_session.revoked_at is not None or current_session.rotated_at is not None:
        db.query(models.AuthSession).filter(
            models.AuthSession.family_id == current_session.family_id,
            models.AuthSession.revoked_at.is_(None),
        ).update({models.AuthSession.revoked_at: now}, synchronize_session=False)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    if current_session.expires_at <= now:
        current_session.revoked_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    user = db.get(User, current_session.user_id)
    if user is None or not is_account_enabled(user):
        current_session.revoked_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    new_refresh_token = generate_opaque_token()
    replacement = models.AuthSession(
        id=str(uuid4()),
        family_id=current_session.family_id,
        refresh_token_hash=hash_opaque_token(new_refresh_token),
        user_id=current_session.user_id,
        tenant_id=current_session.tenant_id,
        created_at=now,
        expires_at=current_session.expires_at,
        client_ip_hash=_client_hash(client_ip),
        user_agent_hash=_client_hash(user_agent),
    )
    db.add(replacement)
    db.flush()
    current_session.last_used_at = now
    current_session.rotated_at = now
    current_session.revoked_at = now
    current_session.replaced_by_session_id = replacement.id
    db.commit()

    return schemas.Token(
        access_token=_access_token_for(user, replacement.id),
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def get_active_session(db: Session, session_id: str, user_id: int) -> models.AuthSession | None:
    now = _utc_now()
    return (
        db.query(models.AuthSession)
        .filter(
            models.AuthSession.id == session_id,
            models.AuthSession.user_id == user_id,
            models.AuthSession.revoked_at.is_(None),
            models.AuthSession.expires_at > now,
        )
        .first()
    )


def revoke_session(db: Session, session_id: str, user_id: int) -> None:
    now = _utc_now()
    db.query(models.AuthSession).filter(
        models.AuthSession.id == session_id,
        models.AuthSession.user_id == user_id,
        models.AuthSession.revoked_at.is_(None),
    ).update({models.AuthSession.revoked_at: now}, synchronize_session=False)
    db.commit()


def revoke_all_sessions(db: Session, user_id: int) -> None:
    db.query(models.AuthSession).filter(
        models.AuthSession.user_id == user_id,
        models.AuthSession.revoked_at.is_(None),
    ).update({models.AuthSession.revoked_at: _utc_now()}, synchronize_session=False)
    db.commit()


def _throttle_keys(email: str, client_ip: str | None) -> list[tuple[str, str]]:
    keys = [("account", hash_sensitive_identifier(normalize_email(email)))]
    if client_ip:
        keys.append(("ip", hash_sensitive_identifier(client_ip)))
    return keys


def _advisory_lock_key(key_hash: str) -> int:
    unsigned = int(key_hash[:16], 16)
    return unsigned if unsigned < 2**63 else unsigned - 2**64


def ensure_login_not_throttled(db: Session, email: str, client_ip: str | None) -> None:
    now = _utc_now()
    for key_type, key_hash in _throttle_keys(email, client_ip):
        db.execute(select(func.pg_advisory_xact_lock(_advisory_lock_key(key_hash))))
        throttle = db.execute(
            select(models.LoginThrottle)
            .where(
                models.LoginThrottle.key_type == key_type,
                models.LoginThrottle.key_hash == key_hash,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if throttle is not None and throttle.blocked_until is not None:
            if throttle.blocked_until > now:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many login attempts. Try again later.",
                    headers={"Retry-After": str(settings.LOGIN_BLOCK_MINUTES * 60)},
                )
    db.rollback()


def record_login_failure(db: Session, email: str, client_ip: str | None) -> bool:
    now = _utc_now()
    window_start = now - timedelta(minutes=settings.LOGIN_WINDOW_MINUTES)
    is_blocked = False
    for key_type, key_hash in _throttle_keys(email, client_ip):
        db.execute(select(func.pg_advisory_xact_lock(_advisory_lock_key(key_hash))))
        throttle = db.execute(
            select(models.LoginThrottle)
            .where(
                models.LoginThrottle.key_type == key_type,
                models.LoginThrottle.key_hash == key_hash,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if throttle is None:
            throttle = models.LoginThrottle(
                key_type=key_type,
                key_hash=key_hash,
                failed_attempts=1,
                window_started_at=now,
                updated_at=now,
            )
            db.add(throttle)
        elif throttle.window_started_at < window_start:
            throttle.failed_attempts = 1
            throttle.window_started_at = now
            throttle.blocked_until = None
            throttle.updated_at = now
        else:
            throttle.failed_attempts += 1
            throttle.updated_at = now

        failure_limit = (
            settings.LOGIN_MAX_FAILURES if key_type == "account" else settings.LOGIN_IP_MAX_FAILURES
        )
        if throttle.failed_attempts >= failure_limit:
            throttle.blocked_until = now + timedelta(minutes=settings.LOGIN_BLOCK_MINUTES)
            is_blocked = True
    db.commit()
    return is_blocked


def clear_account_throttle(db: Session, email: str) -> None:
    key_hash = hash_sensitive_identifier(normalize_email(email))
    db.execute(
        delete(models.LoginThrottle).where(
            models.LoginThrottle.key_type == "account",
            models.LoginThrottle.key_hash == key_hash,
        )
    )
    db.commit()


def _invalidate_account_action_tokens(
    db: Session, model: type, user_id: int, now: datetime
) -> None:
    db.query(model).filter(
        model.user_id == user_id,
        model.used_at.is_(None),
    ).update({model.used_at: now}, synchronize_session=False)


def issue_password_reset(
    db: Session,
    user: User,
    *,
    requested_by_user_id: int,
) -> None:
    now = _utc_now()
    raw_token = generate_opaque_token()
    _invalidate_account_action_tokens(db, models.PasswordResetToken, user.id, now)
    reset_token = models.PasswordResetToken(
        id=str(uuid4()),
        token_hash=hash_opaque_token(raw_token),
        user_id=user.id,
        tenant_id=user.tenant_id,
        requested_by_user_id=requested_by_user_id,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.ACCOUNT_ACTION_TOKEN_EXPIRE_MINUTES),
    )
    db.add(reset_token)
    db.commit()

    reset_url = f"{settings.PASSWORD_RESET_BASE_URL}?{urlencode({'token': raw_token})}"
    try:
        send_account_email(
            user.email,
            "Reset your DawAI password",
            "Use this one-time link within "
            f"{settings.ACCOUNT_ACTION_TOKEN_EXPIRE_MINUTES} minutes:\n\n{reset_url}",
        )
    except Exception:
        reset_token.used_at = _utc_now()
        db.commit()
        raise


def request_password_reset(db: Session, email: str) -> None:
    user = user_crud.get_user_by_email(db, email)
    if user is None or not is_account_enabled(user):
        return
    issue_password_reset(db, user, requested_by_user_id=user.id)


def confirm_password_reset(db: Session, raw_token: str, new_password: str) -> None:
    now = _utc_now()
    reset_token = (
        db.query(models.PasswordResetToken)
        .filter(models.PasswordResetToken.token_hash == hash_opaque_token(raw_token))
        .with_for_update()
        .first()
    )
    if reset_token is None or reset_token.used_at is not None or reset_token.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password-reset token",
        )

    user = db.get(User, reset_token.user_id)
    if user is None or not is_account_enabled(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password-reset token",
        )

    user.hashed_password = get_password_hash(new_password)
    reset_token.used_at = now
    db.query(models.AuthSession).filter(
        models.AuthSession.user_id == user.id,
        models.AuthSession.revoked_at.is_(None),
    ).update({models.AuthSession.revoked_at: now}, synchronize_session=False)
    _invalidate_account_action_tokens(db, models.PasswordResetToken, user.id, now)
    db.commit()


def change_password(db: Session, user_id: int, current_password: str, new_password: str) -> None:
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None or not verify_password(current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if verify_password(new_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password",
        )

    now = _utc_now()
    user.hashed_password = get_password_hash(new_password)
    db.query(models.AuthSession).filter(
        models.AuthSession.user_id == user.id,
        models.AuthSession.revoked_at.is_(None),
    ).update({models.AuthSession.revoked_at: now}, synchronize_session=False)
    _invalidate_account_action_tokens(db, models.PasswordResetToken, user.id, now)
    db.commit()


def update_self_profile(db: Session, user_id: int, full_name: str) -> User:
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.full_name = full_name
    db.commit()
    db.refresh(user)
    return user


def request_email_change(
    db: Session,
    user_id: int,
    current_password: str,
    new_email: str,
) -> None:
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None or not verify_password(current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    normalized_email = normalize_email(new_email)
    if normalize_email(user.email) == normalized_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email must be different from the current email",
        )
    if user_crud.get_user_by_email(db, normalized_email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    now = _utc_now()
    raw_token = generate_opaque_token()
    _invalidate_account_action_tokens(db, models.EmailChangeToken, user.id, now)
    email_token = models.EmailChangeToken(
        id=str(uuid4()),
        token_hash=hash_opaque_token(raw_token),
        user_id=user.id,
        tenant_id=user.tenant_id,
        new_email=normalized_email,
        created_at=now,
        expires_at=now + timedelta(minutes=settings.ACCOUNT_ACTION_TOKEN_EXPIRE_MINUTES),
    )
    db.add(email_token)
    db.commit()

    confirmation_url = f"{settings.EMAIL_CHANGE_BASE_URL}?{urlencode({'token': raw_token})}"
    try:
        send_account_email(
            normalized_email,
            "Confirm your new DawAI email",
            "Use this one-time link within "
            f"{settings.ACCOUNT_ACTION_TOKEN_EXPIRE_MINUTES} minutes:\n\n{confirmation_url}",
        )
    except Exception:
        email_token.used_at = _utc_now()
        db.commit()
        raise


def confirm_email_change(db: Session, raw_token: str) -> None:
    now = _utc_now()
    email_token = (
        db.query(models.EmailChangeToken)
        .filter(models.EmailChangeToken.token_hash == hash_opaque_token(raw_token))
        .with_for_update()
        .first()
    )
    if email_token is None or email_token.used_at is not None or email_token.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired email-change token",
        )
    if user_crud.get_user_by_email(db, email_token.new_email) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = db.query(User).filter(User.id == email_token.user_id).with_for_update().first()
    if user is None or not is_account_enabled(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired email-change token",
        )

    user.email = normalize_email(email_token.new_email)
    email_token.used_at = now
    _invalidate_account_action_tokens(db, models.EmailChangeToken, user.id, now)
    db.query(models.AuthSession).filter(
        models.AuthSession.user_id == user.id,
        models.AuthSession.revoked_at.is_(None),
    ).update({models.AuthSession.revoked_at: now}, synchronize_session=False)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
