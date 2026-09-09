from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin', 'manager', 'pharmacist')",
            name="ck_users_role_canonical",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_users_id_tenant_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)

    # We store the hashed password, never the plain text
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)

    # Canonical roles are super_admin, manager, and pharmacist.
    role = Column(String, default="pharmacist", server_default="pharmacist", nullable=False)
    is_active = Column(Boolean, default=True)

    # Foreign Key connecting this user to a specific pharmacy (Tenant)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # SQLAlchemy relationship for easy access (e.g., user.tenant.name)
    tenant = relationship("Tenant", back_populates="users", foreign_keys=[tenant_id])


Index("uq_users_email_lower", func.lower(User.email), unique=True)
