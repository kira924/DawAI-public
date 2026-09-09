from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base
from src.core.money import MONEY_TYPE


class FinancialTransaction(Base):
    __tablename__ = "financial_transactions"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('sale', 'sales_return', 'customer_payment', "
            "'purchase_receipt', 'purchase_return', 'supplier_payment', "
            "'expense', 'shift_adjustment')",
            name="ck_financial_transactions_event_type_canonical",
        ),
        CheckConstraint(
            "source_type IN ('invoice', 'sales_return', 'customer_payment', "
            "'purchase_order', 'purchase_return', 'supplier_payment', "
            "'expense', 'shift')",
            name="ck_financial_transactions_source_type_canonical",
        ),
        CheckConstraint(
            "char_length(source_key) BETWEEN 1 AND 160",
            name="ck_financial_transactions_source_key_length",
        ),
        CheckConstraint(
            "(event_type = 'sale' AND source_type = 'invoice') OR "
            "(event_type = 'sales_return' AND source_type = 'sales_return') OR "
            "(event_type = 'customer_payment' AND source_type = 'customer_payment') OR "
            "(event_type = 'purchase_receipt' AND source_type = 'purchase_order') OR "
            "(event_type = 'purchase_return' AND source_type = 'purchase_return') OR "
            "(event_type = 'supplier_payment' AND source_type = 'supplier_payment') OR "
            "(event_type = 'expense' AND source_type = 'expense') OR "
            "(event_type = 'shift_adjustment' AND source_type = 'shift')",
            name="ck_financial_transactions_event_source_context",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_financial_transactions_id_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_key",
            name="uq_financial_transactions_tenant_source_key",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_financial_transactions_actor_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["shift_id", "actor_user_id", "tenant_id"],
            ["shifts.id", "shifts.user_id", "shifts.tenant_id"],
            name="fk_financial_transactions_shift_actor_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["customer_id", "tenant_id"],
            ["customers.id", "customers.tenant_id"],
            name="fk_financial_transactions_customer_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "tenant_id"],
            ["suppliers.id", "suppliers.tenant_id"],
            name="fk_financial_transactions_supplier_same_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_financial_transactions_tenant_occurred_id",
            "tenant_id",
            "recorded_at",
            "id",
        ),
        Index("ix_financial_transactions_tenant_shift", "tenant_id", "shift_id"),
        Index("ix_financial_transactions_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_financial_transactions_tenant_supplier", "tenant_id", "supplier_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_type = Column(String(40), nullable=False)
    source_type = Column(String(40), nullable=False)
    source_id = Column(BigInteger, nullable=False)
    source_key = Column(String(160), nullable=False)
    actor_user_id = Column(Integer, nullable=False)
    shift_id = Column(Integer, nullable=True)
    customer_id = Column(Integer, nullable=True)
    supplier_id = Column(Integer, nullable=True)
    tenant_id = Column(Integer, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=True, server_default=func.now())
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    entries = relationship(
        "FinancialEntry",
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="FinancialEntry.id",
    )


class FinancialEntry(Base):
    __tablename__ = "financial_entries"
    __table_args__ = (
        CheckConstraint(
            "account_code IN ('cash_drawer', 'customer_receivable', 'customer_credit', "
            "'supplier_payable', 'sales_revenue', 'sales_returns', "
            "'inventory_purchases', 'operating_expense', 'external_funds', "
            "'cash_over_short')",
            name="ck_financial_entries_account_canonical",
        ),
        CheckConstraint(
            "entry_side IN ('debit', 'credit')",
            name="ck_financial_entries_side_canonical",
        ),
        CheckConstraint("amount > 0", name="ck_financial_entries_amount_positive"),
        UniqueConstraint(
            "transaction_id",
            "account_code",
            "entry_side",
            name="uq_financial_entries_transaction_account_side",
        ),
        ForeignKeyConstraint(
            ["transaction_id", "tenant_id"],
            ["financial_transactions.id", "financial_transactions.tenant_id"],
            name="fk_financial_entries_transaction_same_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_financial_entries_tenant_account", "tenant_id", "account_code"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id = Column(BigInteger, nullable=False)
    account_code = Column(String(40), nullable=False)
    entry_side = Column(String(8), nullable=False)
    amount = Column(MONEY_TYPE, nullable=False)
    tenant_id = Column(Integer, nullable=False)

    transaction = relationship("FinancialTransaction", back_populates="entries")
