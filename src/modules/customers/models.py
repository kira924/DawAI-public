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
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.money import MONEY_TYPE


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("credit_limit >= 0", name="ck_customers_credit_limit_nonnegative"),
        CheckConstraint("total_debt >= 0", name="ck_customers_total_debt_nonnegative"),
        CheckConstraint("credit_balance >= 0", name="ck_customers_credit_balance_nonnegative"),
        UniqueConstraint("id", "tenant_id", name="uq_customers_id_tenant_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    phone = Column(String, nullable=True)

    # Financial tracking for debts
    credit_limit = Column(MONEY_TYPE, default=0, server_default="0", nullable=False)
    total_debt = Column(MONEY_TYPE, default=0, server_default="0", nullable=False)
    credit_balance = Column(MONEY_TYPE, default=0, server_default="0", nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    # Relationships
    payments = relationship(
        "CustomerPayment", back_populates="customer", cascade="all, delete-orphan"
    )


class CustomerPayment(Base):
    __tablename__ = "customer_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_customer_payments_amount_positive"),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_customer_payments_idempotency_key_length",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64",
            name="ck_customer_payments_request_fingerprint_length",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_customer_payments_id_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_customer_payments_tenant_idempotency_key",
        ),
        UniqueConstraint(
            "id",
            "customer_id",
            "tenant_id",
            name="uq_customer_payments_id_customer_tenant_id",
        ),
        ForeignKeyConstraint(
            ["customer_id", "tenant_id"],
            ["customers.id", "customers.tenant_id"],
            name="fk_customer_payments_customer_same_tenant",
        ),
        ForeignKeyConstraint(
            ["shift_id", "user_id", "tenant_id"],
            ["shifts.id", "shifts.user_id", "shifts.tenant_id"],
            name="fk_customer_payments_shift_user_same_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_customer_payments_user_same_tenant",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(
        String(64), nullable=False, server_default=text("gen_random_uuid()::text")
    )
    request_fingerprint = Column(String(64), nullable=False, server_default=text("repeat('0', 64)"))
    amount = Column(MONEY_TYPE, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Traceability fields linking the payment to the exact shift and user
    customer_id = Column(Integer, nullable=False)
    shift_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    # Relationships
    customer = relationship("Customer", back_populates="payments")


class CustomerPaymentAllocation(Base):
    __tablename__ = "customer_payment_allocations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_customer_payment_allocations_amount_positive"),
        UniqueConstraint(
            "payment_id",
            "invoice_id",
            name="uq_customer_payment_allocations_payment_invoice",
        ),
        ForeignKeyConstraint(
            ["payment_id", "customer_id", "tenant_id"],
            [
                "customer_payments.id",
                "customer_payments.customer_id",
                "customer_payments.tenant_id",
            ],
            name="fk_customer_payment_allocations_payment_context",
        ),
        ForeignKeyConstraint(
            ["invoice_id", "customer_id", "tenant_id"],
            ["invoices.id", "invoices.customer_id", "invoices.tenant_id"],
            name="fk_customer_payment_allocations_invoice_context",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, nullable=False)
    invoice_id = Column(Integer, nullable=False)
    customer_id = Column(Integer, nullable=False)
    amount = Column(MONEY_TYPE, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
