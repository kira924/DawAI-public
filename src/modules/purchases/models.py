from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.money import MONEY_TYPE, PERCENTAGE_TYPE


# 1. Supplier Table
class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_suppliers_id_tenant_id"),)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    contact_info = Column(String, nullable=True)

    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    purchases = relationship("PurchaseOrder", back_populates="supplier")
    # New relationships for the complete financial cycle
    payments = relationship("SupplierPayment", back_populates="supplier")
    returns = relationship("PurchaseReturn", back_populates="supplier")


# 2. Purchase Order Header Table
class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint("subtotal >= 0", name="ck_purchase_orders_subtotal_nonnegative"),
        CheckConstraint(
            "total_discount >= 0",
            name="ck_purchase_orders_discount_nonnegative",
        ),
        CheckConstraint(
            "tax_percentage >= 0 AND tax_percentage <= 100",
            name="ck_purchase_orders_tax_percentage_range",
        ),
        CheckConstraint("tax_value >= 0", name="ck_purchase_orders_tax_value_nonnegative"),
        CheckConstraint("total_cost >= 0", name="ck_purchase_orders_total_cost_nonnegative"),
        CheckConstraint(
            "status IN ('PENDING', 'RECEIVED', 'CANCELLED')",
            name="ck_purchase_orders_status_canonical",
        ),
        CheckConstraint(
            "subtotal >= total_discount",
            name="ck_purchase_orders_discount_not_above_subtotal",
        ),
        CheckConstraint(
            "tax_value = round((subtotal - total_discount) * tax_percentage / 100, 2)",
            name="ck_purchase_orders_tax_matches_net_subtotal",
        ),
        CheckConstraint(
            "tax_percentage = 0 AND tax_value = 0",
            name="ck_purchase_orders_tax_free_mvp",
        ),
        CheckConstraint(
            "total_cost = subtotal - total_discount + tax_value",
            name="ck_purchase_orders_total_matches_components",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_purchase_orders_id_tenant_id"),
        UniqueConstraint(
            "id",
            "supplier_id",
            "tenant_id",
            name="uq_purchase_orders_id_supplier_tenant_id",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "tenant_id"],
            ["suppliers.id", "suppliers.tenant_id"],
            name="fk_purchase_orders_supplier_same_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_purchase_orders_user_same_tenant",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    # Order status (PENDING, RECEIVED, CANCELLED)
    status = Column(String, default="PENDING", server_default="PENDING", nullable=False)

    subtotal = Column(MONEY_TYPE, nullable=False, server_default="0")
    total_discount = Column(MONEY_TYPE, nullable=False, server_default="0")

    # New Tax Columns
    tax_percentage = Column(PERCENTAGE_TYPE, nullable=False, server_default="0")
    tax_value = Column(MONEY_TYPE, nullable=False, server_default="0")

    total_cost = Column(MONEY_TYPE, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    supplier_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    supplier = relationship("Supplier", back_populates="purchases")
    items = relationship(
        "PurchaseItem", back_populates="purchase_order", cascade="all, delete-orphan"
    )


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_purchase_items_quantity_positive"),
        CheckConstraint("base_cost >= 0", name="ck_purchase_items_base_cost_nonnegative"),
        CheckConstraint(
            "discount_percentage >= 0 AND discount_percentage <= 100",
            name="ck_purchase_items_discount_percentage_range",
        ),
        CheckConstraint(
            "discount_value >= 0",
            name="ck_purchase_items_discount_value_nonnegative",
        ),
        CheckConstraint("unit_cost >= 0", name="ck_purchase_items_unit_cost_nonnegative"),
        CheckConstraint("subtotal >= 0", name="ck_purchase_items_subtotal_nonnegative"),
        CheckConstraint(
            "unit_cost = round(base_cost * (100 - discount_percentage) / 100, 2)",
            name="ck_purchase_items_unit_cost_matches_discount",
        ),
        CheckConstraint(
            "subtotal = unit_cost * quantity",
            name="ck_purchase_items_subtotal_matches_unit_cost",
        ),
        CheckConstraint(
            "discount_value = base_cost * quantity - subtotal",
            name="ck_purchase_items_discount_matches_subtotal",
        ),
        UniqueConstraint(
            "id",
            "purchase_id",
            "product_id",
            "received_batch_id",
            "tenant_id",
            name="uq_purchase_items_return_context",
        ),
        ForeignKeyConstraint(
            ["purchase_id", "tenant_id"],
            ["purchase_orders.id", "purchase_orders.tenant_id"],
            name="fk_purchase_items_order_same_tenant",
        ),
        ForeignKeyConstraint(
            ["product_id", "tenant_id"],
            ["products.id", "products.tenant_id"],
            name="fk_purchase_items_product_same_tenant",
        ),
        ForeignKeyConstraint(
            ["received_batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_purchase_items_received_batch_context",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)
    received_batch_id = Column(Integer, nullable=True)
    tenant_id = Column(Integer, nullable=False)

    quantity = Column(Integer, nullable=False)

    # Item level accounting
    base_cost = Column(MONEY_TYPE, nullable=False, server_default="0")
    discount_percentage = Column(PERCENTAGE_TYPE, nullable=False, server_default="0")
    discount_value = Column(MONEY_TYPE, nullable=False, server_default="0")

    # unit_cost here represents the Net Cost after discount
    unit_cost = Column(MONEY_TYPE, nullable=False)
    subtotal = Column(MONEY_TYPE, nullable=False)

    batch_number = Column(String, nullable=True)
    expiry_date = Column(Date, nullable=True)

    purchase_order = relationship("PurchaseOrder", back_populates="items")


# 3. Supplier Payments (Accounts Payable)
class SupplierPayment(Base):
    __tablename__ = "supplier_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_supplier_payments_amount_positive"),
        CheckConstraint(
            "payment_channel IN ('cash', 'external')",
            name="ck_supplier_payments_channel_canonical",
        ),
        CheckConstraint(
            "(payment_channel = 'cash' AND shift_id IS NOT NULL) OR "
            "(payment_channel = 'external' AND shift_id IS NULL)",
            name="ck_supplier_payments_channel_shift_context",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_supplier_payments_tenant_idempotency_key",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "tenant_id"],
            ["suppliers.id", "suppliers.tenant_id"],
            name="fk_supplier_payments_supplier_same_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_supplier_payments_user_same_tenant",
        ),
        ForeignKeyConstraint(
            ["shift_id", "user_id", "tenant_id"],
            ["shifts.id", "shifts.user_id", "shifts.tenant_id"],
            name="fk_supplier_payments_shift_user_same_tenant",
            ondelete="RESTRICT",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    amount = Column(MONEY_TYPE, nullable=False)
    payment_channel = Column(
        String(16), nullable=False, default="external", server_default="external"
    )
    shift_id = Column(Integer, nullable=True)
    payment_date = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reference_number = Column(String, nullable=True)  # E.g., paper receipt number

    supplier_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    supplier = relationship("Supplier", back_populates="payments")


# 4. Purchase Returns Header
class PurchaseReturn(Base):
    __tablename__ = "purchase_returns"
    __table_args__ = (
        CheckConstraint(
            "total_refund >= 0",
            name="ck_purchase_returns_total_refund_nonnegative",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_purchase_returns_id_tenant_id"),
        UniqueConstraint(
            "id",
            "purchase_order_id",
            "tenant_id",
            name="uq_purchase_returns_id_order_tenant_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_purchase_returns_tenant_idempotency_key",
        ),
        ForeignKeyConstraint(
            ["purchase_order_id", "supplier_id", "tenant_id"],
            ["purchase_orders.id", "purchase_orders.supplier_id", "purchase_orders.tenant_id"],
            name="fk_purchase_returns_order_supplier_same_tenant",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "tenant_id"],
            ["suppliers.id", "suppliers.tenant_id"],
            name="fk_purchase_returns_supplier_same_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_purchase_returns_user_same_tenant",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    total_refund = Column(MONEY_TYPE, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    purchase_order_id = Column(Integer, nullable=False)
    supplier_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    supplier = relationship("Supplier", back_populates="returns")
    items = relationship(
        "PurchaseReturnItem", back_populates="purchase_return", cascade="all, delete-orphan"
    )


# 5. Purchase Returns Details
class PurchaseReturnItem(Base):
    __tablename__ = "purchase_return_items"
    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_purchase_return_items_quantity_positive",
        ),
        CheckConstraint(
            "unit_refund >= 0",
            name="ck_purchase_return_items_unit_refund_nonnegative",
        ),
        CheckConstraint(
            "subtotal >= 0",
            name="ck_purchase_return_items_subtotal_nonnegative",
        ),
        CheckConstraint(
            "subtotal = unit_refund * quantity",
            name="ck_purchase_return_items_subtotal_matches_unit_refund",
        ),
        ForeignKeyConstraint(
            ["return_id", "purchase_order_id", "tenant_id"],
            [
                "purchase_returns.id",
                "purchase_returns.purchase_order_id",
                "purchase_returns.tenant_id",
            ],
            name="fk_purchase_return_items_return_context",
        ),
        ForeignKeyConstraint(
            ["purchase_item_id", "purchase_order_id", "product_id", "batch_id", "tenant_id"],
            [
                "purchase_items.id",
                "purchase_items.purchase_id",
                "purchase_items.product_id",
                "purchase_items.received_batch_id",
                "purchase_items.tenant_id",
            ],
            name="fk_purchase_return_items_purchase_item_context",
        ),
        ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_purchase_return_items_batch_context",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    return_id = Column(Integer, nullable=False)
    purchase_order_id = Column(Integer, nullable=False)
    purchase_item_id = Column(Integer, nullable=False)
    batch_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, nullable=False)

    quantity = Column(Integer, nullable=False)
    unit_refund = Column(MONEY_TYPE, nullable=False)
    subtotal = Column(MONEY_TYPE, nullable=False)

    purchase_return = relationship("PurchaseReturn", back_populates="items")
