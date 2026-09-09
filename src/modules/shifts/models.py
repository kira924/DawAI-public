from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.sql import func

from src.core.database import Base
from src.core.money import MONEY_TYPE


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'CLOSED')",
            name="ck_shifts_status_canonical",
        ),
        CheckConstraint(
            "opening_balance >= 0",
            name="ck_shifts_opening_balance_nonnegative",
        ),
        CheckConstraint(
            "expected_closing_balance >= 0",
            name="ck_shifts_expected_balance_nonnegative",
        ),
        CheckConstraint(
            "actual_closing_balance IS NULL OR actual_closing_balance >= 0",
            name="ck_shifts_actual_balance_nonnegative",
        ),
        CheckConstraint(
            "(status = 'OPEN' AND end_time IS NULL AND actual_closing_balance IS NULL "
            "AND difference IS NULL) OR "
            "(status = 'CLOSED' AND end_time IS NOT NULL AND actual_closing_balance IS NOT NULL "
            "AND difference IS NOT NULL)",
            name="ck_shifts_lifecycle_consistent",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_shifts_user_same_tenant",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "user_id", "tenant_id", name="uq_shifts_id_user_id_tenant_id"),
        Index(
            "uq_shifts_user_open",
            "user_id",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    # Shift status: OPEN or CLOSED
    status = Column(String, nullable=False, default="OPEN", server_default="OPEN")

    # Timestamps for shift lifecycle
    start_time = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    end_time = Column(DateTime(timezone=True), nullable=True)

    # Financial tracking parameters
    opening_balance = Column(MONEY_TYPE, nullable=False, default=0, server_default="0")
    expected_closing_balance = Column(MONEY_TYPE, nullable=False, default=0, server_default="0")
    actual_closing_balance = Column(MONEY_TYPE, nullable=True)

    # Shortage or overage in the drawer
    difference = Column(MONEY_TYPE, nullable=True)

    # Foreign Keys connecting the shift to the cashier and the pharmacy branch
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    # Note: Later we will add a relationship in the Invoice model to link sales to shifts
