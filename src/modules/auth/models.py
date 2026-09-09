from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from src.core.database import Base


def _uuid_string() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_auth_sessions_user_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expiry_order"),
        Index("ix_auth_sessions_user_active", "user_id", "revoked_at", "expires_at"),
        Index("ix_auth_sessions_family_id", "family_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_string)
    family_id = Column(String(36), nullable=False, default=_uuid_string)
    refresh_token_hash = Column(String(64), nullable=False, unique=True)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    replaced_by_session_id = Column(
        String(36),
        ForeignKey("auth_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_ip_hash = Column(String(64), nullable=True)
    user_agent_hash = Column(String(64), nullable=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    __table_args__ = (
        CheckConstraint("key_type IN ('account', 'ip')", name="ck_login_throttles_key_type"),
        CheckConstraint("failed_attempts >= 0", name="ck_login_throttles_failures_nonnegative"),
        UniqueConstraint("key_type", "key_hash", name="uq_login_throttles_key"),
    )

    id = Column(Integer, primary_key=True)
    key_type = Column(String(16), nullable=False)
    key_hash = Column(String(64), nullable=False)
    failed_attempts = Column(Integer, nullable=False, default=0)
    window_started_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    blocked_until = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_password_reset_tokens_user_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["requested_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_password_reset_tokens_requester_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint("expires_at > created_at", name="ck_password_reset_tokens_expiry_order"),
        Index("ix_password_reset_tokens_user_active", "user_id", "used_at", "expires_at"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_string)
    token_hash = Column(String(64), nullable=False, unique=True)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    requested_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)


class EmailChangeToken(Base):
    __tablename__ = "email_change_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_email_change_tokens_user_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint("expires_at > created_at", name="ck_email_change_tokens_expiry_order"),
        Index("ix_email_change_tokens_user_active", "user_id", "used_at", "expires_at"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_string)
    token_hash = Column(String(64), nullable=False, unique=True)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    new_email = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)


class SupportAccessGrant(Base):
    __tablename__ = "support_access_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["granted_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_support_access_grants_grantor_same_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "scope IN ('read_only', 'operations')",
            name="ck_support_access_grants_scope",
        ),
        CheckConstraint("expires_at > created_at", name="ck_support_access_grants_expiry_order"),
        Index("ix_support_access_grants_active", "support_user_id", "revoked_at", "expires_at"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_string)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    support_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    granted_by_user_id = Column(Integer, nullable=False)
    scope = Column(String(16), nullable=False, default="read_only")
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)


class SupportAccessAudit(Base):
    __tablename__ = "support_access_audits"
    __table_args__ = (
        Index("ix_support_access_audits_grant_occurred", "support_grant_id", "occurred_at"),
        Index("ix_support_access_audits_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    support_grant_id = Column(
        String(36),
        ForeignKey("support_access_grants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    support_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    event_type = Column(String(32), nullable=False)
    request_method = Column(String(16), nullable=True)
    request_path = Column(String(512), nullable=True)
    detail = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_utc_now)
