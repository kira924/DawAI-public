from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from src.core.database import Base
from src.core.money import MONEY_TYPE


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_expenses_idempotency_key_length",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64",
            name="ck_expenses_request_fingerprint_length",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_expenses_id_tenant_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_expenses_tenant_idempotency_key"),
        ForeignKeyConstraint(
            ["shift_id", "user_id", "tenant_id"],
            ["shifts.id", "shifts.user_id", "shifts.tenant_id"],
            name="fk_expenses_shift_user_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_expenses_user_same_tenant",
            ondelete="RESTRICT",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(
        String(64), nullable=False, server_default=text("gen_random_uuid()::text")
    )
    request_fingerprint = Column(String(64), nullable=False, server_default=text("repeat('0', 64)"))

    # Financial details
    amount = Column(MONEY_TYPE, nullable=False)
    description = Column(String, nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Traceability fields linking the expense to the exact shift, user, and branch
    shift_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
