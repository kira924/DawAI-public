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
from sqlalchemy.sql import func

from src.core.database import Base
from src.core.money import MONEY_TYPE


# 1. Invoice Header Table
class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(
            "payment_type IN ('cash', 'credit')",
            name="ck_invoices_payment_type_canonical",
        ),
        CheckConstraint("total_amount > 0", name="ck_invoices_total_amount_positive"),
        CheckConstraint(
            "outstanding_amount >= 0 AND outstanding_amount <= total_amount",
            name="ck_invoices_outstanding_amount_range",
        ),
        CheckConstraint(
            "(payment_type = 'cash' AND outstanding_amount = 0) "
            "OR (payment_type = 'credit' AND customer_id IS NOT NULL)",
            name="ck_invoices_payment_outstanding_consistency",
        ),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_invoices_idempotency_key_length",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64",
            name="ck_invoices_request_fingerprint_length",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_invoices_id_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_invoices_tenant_idempotency_key",
        ),
        UniqueConstraint(
            "id",
            "customer_id",
            "tenant_id",
            name="uq_invoices_id_customer_tenant_id",
        ),
        ForeignKeyConstraint(
            ["customer_id", "tenant_id"],
            ["customers.id", "customers.tenant_id"],
            name="fk_invoices_customer_same_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_invoices_user_same_tenant",
        ),
        ForeignKeyConstraint(
            ["shift_id", "user_id", "tenant_id"],
            ["shifts.id", "shifts.user_id", "shifts.tenant_id"],
            name="fk_invoices_shift_user_same_tenant",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(
        String(64),
        nullable=False,
        server_default=text("gen_random_uuid()::text"),
    )
    request_fingerprint = Column(
        String(64),
        nullable=False,
        server_default=text("repeat('0', 64)"),
    )
    total_amount = Column(MONEY_TYPE, nullable=False)
    outstanding_amount = Column(MONEY_TYPE, nullable=False, default=0, server_default="0")

    payment_type = Column(String, default="cash", server_default="cash", nullable=False)
    customer_id = Column(Integer, nullable=True)

    # Store the exact time the sale happened using UTC
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Track which pharmacist/cashier made the sale
    user_id = Column(Integer, nullable=False)

    # Strict tenant isolation
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    shift_id = Column(Integer, nullable=True)

    # Establish a one-to-many relationship with the items table
    # cascade="all, delete-orphan" ensures if an invoice is deleted, its items are cleanly removed
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


# 2. Invoice Details (Items) Table
class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["invoice_id", "tenant_id"],
            ["invoices.id", "invoices.tenant_id"],
            name="fk_invoice_items_invoice_same_tenant",
        ),
        ForeignKeyConstraint(
            ["product_id", "tenant_id"],
            ["products.id", "products.tenant_id"],
            name="fk_invoice_items_product_same_tenant",
        ),
        CheckConstraint("quantity > 0", name="ck_invoice_items_quantity_positive"),
        CheckConstraint("list_unit_price >= 0", name="ck_invoice_items_list_price_nonnegative"),
        CheckConstraint("sale_unit_price >= 0", name="ck_invoice_items_sale_price_nonnegative"),
        CheckConstraint(
            "sale_unit_price <= list_unit_price",
            name="ck_invoice_items_sale_price_not_above_list",
        ),
        CheckConstraint("subtotal >= 0", name="ck_invoice_items_subtotal_nonnegative"),
        CheckConstraint(
            "subtotal = sale_unit_price * quantity",
            name="ck_invoice_items_subtotal_matches_sale_price",
        ),
        UniqueConstraint(
            "id",
            "invoice_id",
            "product_id",
            "tenant_id",
            name="uq_invoice_items_identity_context",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    # Link back to the parent invoice
    invoice_id = Column(Integer, nullable=False)

    # Link to the specific product from the inventory
    product_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, nullable=False)

    # How many parts/strips/boxes were sold? (Always measured in total_parts)
    quantity = Column(Integer, nullable=False)

    list_unit_price = Column(MONEY_TYPE, nullable=False)
    sale_unit_price = Column(MONEY_TYPE, nullable=False)
    subtotal = Column(MONEY_TYPE, nullable=False)

    # Establish the inverse relationship back to the invoice header
    invoice = relationship("Invoice", back_populates="items")
    batch_allocations = relationship(
        "InvoiceItemBatchAllocation",
        back_populates="invoice_item",
        cascade="all, delete-orphan",
    )


class InvoiceItemBatchAllocation(Base):
    __tablename__ = "invoice_item_batch_allocations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_invoice_batch_allocations_quantity_positive"),
        UniqueConstraint(
            "invoice_item_id",
            "batch_id",
            name="uq_invoice_batch_allocations_item_batch",
        ),
        ForeignKeyConstraint(
            ["invoice_item_id", "invoice_id", "product_id", "tenant_id"],
            [
                "invoice_items.id",
                "invoice_items.invoice_id",
                "invoice_items.product_id",
                "invoice_items.tenant_id",
            ],
            name="fk_invoice_batch_allocations_item_context",
        ),
        ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_invoice_batch_allocations_batch_context",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    invoice_item_id = Column(Integer, nullable=False)
    invoice_id = Column(Integer, nullable=False)
    batch_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    quantity = Column(Integer, nullable=False)

    invoice_item = relationship("InvoiceItem", back_populates="batch_allocations")


class SalesReturn(Base):
    __tablename__ = "sales_returns"
    __table_args__ = (
        CheckConstraint("total_refund >= 0", name="ck_sales_returns_total_refund_nonnegative"),
        CheckConstraint("cash_refund >= 0", name="ck_sales_returns_cash_refund_nonnegative"),
        CheckConstraint("debt_reduction >= 0", name="ck_sales_returns_debt_reduction_nonnegative"),
        CheckConstraint(
            "credit_balance_increase >= 0",
            name="ck_sales_returns_credit_balance_increase_nonnegative",
        ),
        CheckConstraint(
            "total_refund = cash_refund + debt_reduction + credit_balance_increase",
            name="ck_sales_returns_refund_breakdown_matches_total",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_sales_returns_id_tenant_id"),
        UniqueConstraint(
            "id", "invoice_id", "tenant_id", name="uq_sales_returns_id_invoice_tenant_id"
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_sales_returns_tenant_idempotency_key"
        ),
        ForeignKeyConstraint(
            ["invoice_id", "tenant_id"],
            ["invoices.id", "invoices.tenant_id"],
            name="fk_sales_returns_invoice_same_tenant",
        ),
        ForeignKeyConstraint(
            ["shift_id", "user_id", "tenant_id"],
            ["shifts.id", "shifts.user_id", "shifts.tenant_id"],
            name="fk_sales_returns_shift_user_same_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_sales_returns_user_same_tenant",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    total_refund = Column(MONEY_TYPE, nullable=False)
    cash_refund = Column(MONEY_TYPE, nullable=False, default=0, server_default="0")
    debt_reduction = Column(MONEY_TYPE, nullable=False, default=0, server_default="0")
    credit_balance_increase = Column(MONEY_TYPE, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Foreign Keys
    invoice_id = Column(Integer, nullable=False)
    shift_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    # Relationships
    invoice = relationship("Invoice")
    items = relationship(
        "SalesReturnItem", back_populates="sales_return", cascade="all, delete-orphan"
    )


class SalesReturnItem(Base):
    __tablename__ = "sales_return_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_return_items_quantity_positive"),
        CheckConstraint("unit_refund >= 0", name="ck_sales_return_items_unit_refund_nonnegative"),
        CheckConstraint("refund_amount >= 0", name="ck_sales_return_items_refund_nonnegative"),
        CheckConstraint(
            "refund_amount = unit_refund * quantity",
            name="ck_sales_return_items_refund_matches_unit_price",
        ),
        CheckConstraint(
            "condition IN ('sealed', 'opened_or_damaged', 'unknown', 'legacy')",
            name="ck_sales_return_items_condition_canonical",
        ),
        CheckConstraint(
            "disposition IN ('restock', 'quarantine', 'legacy')",
            name="ck_sales_return_items_disposition_canonical",
        ),
        CheckConstraint(
            "(condition = 'sealed' AND disposition IN ('restock', 'quarantine') "
            "AND batch_id IS NOT NULL) "
            "OR (condition IN ('opened_or_damaged', 'unknown') AND disposition = 'quarantine') "
            "OR (condition = 'legacy' AND disposition = 'legacy')",
            name="ck_sales_return_items_condition_disposition",
        ),
        UniqueConstraint(
            "id", "product_id", "tenant_id", name="uq_sales_return_items_id_product_tenant_id"
        ),
        ForeignKeyConstraint(
            ["sales_return_id", "invoice_id", "tenant_id"],
            ["sales_returns.id", "sales_returns.invoice_id", "sales_returns.tenant_id"],
            name="fk_sales_return_items_return_context",
        ),
        ForeignKeyConstraint(
            ["invoice_item_id", "invoice_id", "product_id", "tenant_id"],
            [
                "invoice_items.id",
                "invoice_items.invoice_id",
                "invoice_items.product_id",
                "invoice_items.tenant_id",
            ],
            name="fk_sales_return_items_invoice_item_context",
        ),
        ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_sales_return_items_batch_context",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    sales_return_id = Column(Integer, nullable=False)
    invoice_id = Column(Integer, nullable=False)
    invoice_item_id = Column(Integer, nullable=False)
    batch_id = Column(Integer, nullable=True)
    product_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, nullable=False)

    quantity = Column(Integer, nullable=False)
    condition = Column(String(32), nullable=False)
    disposition = Column(String(32), nullable=False)
    unit_refund = Column(MONEY_TYPE, nullable=False)
    refund_amount = Column(MONEY_TYPE, nullable=False)

    # Relationships
    sales_return = relationship("SalesReturn", back_populates="items")


class QuarantineItem(Base):
    __tablename__ = "quarantine_items"
    __table_args__ = (
        CheckConstraint(
            "initial_quantity > 0 AND quantity >= 0 AND quantity <= initial_quantity",
            name="ck_quarantine_items_quantity_bounds",
        ),
        CheckConstraint(
            "condition IN ('sealed', 'opened_or_damaged', 'unknown')",
            name="ck_quarantine_items_condition_canonical",
        ),
        CheckConstraint(
            "reason IN ('expired_batch', 'return_condition', 'damage', 'recall', "
            "'quality_concern', 'other')",
            name="ck_quarantine_items_reason_canonical",
        ),
        CheckConstraint(
            "(condition = 'sealed' AND reason = 'expired_batch') "
            "OR (condition IN ('opened_or_damaged', 'unknown') "
            "AND reason = 'return_condition') "
            "OR sales_return_item_id IS NULL",
            name="ck_quarantine_items_reason_context",
        ),
        CheckConstraint(
            "(quantity > 0 AND status = 'pending') OR (quantity = 0 AND status = 'resolved')",
            name="ck_quarantine_items_status_context",
        ),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_quarantine_items_idempotency_key_length",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64",
            name="ck_quarantine_items_request_fingerprint_length",
        ),
        UniqueConstraint("sales_return_item_id", name="uq_quarantine_items_return_item"),
        UniqueConstraint("id", "product_id", "tenant_id", name="uq_quarantine_items_context"),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_quarantine_items_tenant_idempotency_key"
        ),
        ForeignKeyConstraint(
            ["sales_return_item_id", "product_id", "tenant_id"],
            [
                "sales_return_items.id",
                "sales_return_items.product_id",
                "sales_return_items.tenant_id",
            ],
            name="fk_quarantine_items_return_item_context",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_quarantine_items_creator_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_quarantine_items_batch_context",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    sales_return_item_id = Column(Integer, nullable=True)
    product_id = Column(Integer, nullable=False)
    batch_id = Column(Integer, nullable=True)
    quantity = Column(Integer, nullable=False)
    initial_quantity = Column(Integer, nullable=False)
    condition = Column(String(32), nullable=False)
    reason = Column(
        String(32),
        nullable=False,
        default="return_condition",
        server_default="return_condition",
    )
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    idempotency_key = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    created_by_user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
