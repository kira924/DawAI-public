from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    false,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "id"],
            ["users.id", "users.tenant_id"],
            name="fk_tenants_owner_same_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "is_active IS NOT TRUE OR owner_user_id IS NOT NULL",
            name="ck_tenants_active_requires_owner",
        ),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, index=True, nullable=False)
    owner_email = Column(String, index=True, nullable=False)
    owner_user_id = Column(Integer, index=True, nullable=True)
    subscription_plan = Column(String, default="Free")
    is_active = Column(Boolean, default=False, server_default=false(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", back_populates="tenant", foreign_keys="User.tenant_id")
    owner = relationship(
        "User",
        foreign_keys=[owner_user_id],
        primaryjoin="Tenant.owner_user_id == User.id",
        post_update=True,
    )


class TenantOwnershipTransfer(Base):
    __tablename__ = "tenant_ownership_transfers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["previous_owner_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_ownership_transfers_previous_owner_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["new_owner_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_ownership_transfers_new_owner_same_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_ownership_transfers_tenant_created_at", "tenant_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    previous_owner_user_id = Column(Integer)
    new_owner_user_id = Column(Integer, nullable=False)
    performed_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
