import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base
from src.core.money import MONEY_TYPE


class ProductCategory(str, enum.Enum):
    MEDICINE = "medicine"
    COSMETIC = "cosmetic"
    MEDICAL_SUPPLY = "medical_supply"
    BABY_CARE = "baby_care"
    OTHER = "other"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("total_parts >= 0", name="ck_products_total_parts_nonnegative"),
        CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
        CheckConstraint(
            "part_price IS NULL OR part_price >= 0",
            name="ck_products_part_price_nonnegative",
        ),
        CheckConstraint(
            "return_policy IN ('standard', 'unopened_only', 'non_returnable')",
            name="ck_products_return_policy_canonical",
        ),
        CheckConstraint(
            "(reorder_point_parts IS NULL AND target_stock_parts IS NULL) OR "
            "(reorder_point_parts IS NOT NULL AND target_stock_parts IS NOT NULL "
            "AND reorder_point_parts >= 0 AND target_stock_parts > reorder_point_parts)",
            name="ck_products_replenishment_policy_context",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_products_id_tenant_id"),
        Index(
            "uq_products_tenant_catalog_product",
            "tenant_id",
            "catalog_product_id",
            unique=True,
            postgresql_where=text("catalog_product_id IS NOT NULL"),
        ),
        Index(
            "ix_products_tenant_replenishment_configured",
            "tenant_id",
            "reorder_point_parts",
            "id",
            postgresql_where=text(
                "reorder_point_parts IS NOT NULL AND target_stock_parts IS NOT NULL"
            ),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    barcode = Column(String, index=True, nullable=True)
    category = Column(Enum(ProductCategory), default=ProductCategory.MEDICINE, nullable=False)

    # 1. Full box price
    price = Column(MONEY_TYPE, nullable=False)
    # 2. Total boxes in stock
    total_parts = Column(Integer, default=0, server_default="0", nullable=False)
    reorder_point_parts = Column(Integer, nullable=True)
    target_stock_parts = Column(Integer, nullable=True)

    # 3. Flag to check if the product can be opened and sold by pieces
    is_divisible = Column(Boolean, default=False)
    # 4. How many strips/ampoules inside one full box?
    parts_per_unit = Column(Integer, default=1)
    # 5. What do we call this part? (e.g., 'strip', 'ampoule', 'sachet')
    part_name = Column(String, nullable=True)
    # 6. Price per single part
    part_price = Column(MONEY_TYPE, nullable=True)

    return_policy = Column(String, nullable=False, default="standard", server_default="standard")

    expiry_date = Column(Date, nullable=True)
    catalog_product_id = Column(
        BigInteger,
        ForeignKey("catalog_products.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)


class ProductBatch(Base):
    __tablename__ = "product_batches"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_product_batches_quantity_nonnegative"),
        UniqueConstraint(
            "id", "product_id", "tenant_id", name="uq_product_batches_id_product_tenant_id"
        ),
        UniqueConstraint(
            "product_id",
            "batch_number",
            "tenant_id",
            name="uq_product_batches_product_batch_tenant_id",
        ),
        ForeignKeyConstraint(
            ["product_id", "tenant_id"],
            ["products.id", "products.tenant_id"],
            name="fk_product_batches_product_same_tenant",
        ),
        Index(
            "ix_product_batches_tenant_product_expiry_positive",
            "tenant_id",
            "product_id",
            "expiry_date",
            "id",
            postgresql_where=text("quantity > 0"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    batch_number = Column(String, index=True, nullable=False)
    expiry_date = Column(Date, nullable=False)
    quantity = Column(Integer, nullable=False, default=0)

    product_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)

    # Optional: Add relationship to Product class if you want
    # product = relationship("Product", back_populates="batches")


class NearExpiryReceiptConfirmation(Base):
    __tablename__ = "near_expiry_receipt_confirmations"
    __table_args__ = (
        CheckConstraint(
            "source IN ('opening_stock', 'purchase_receipt')",
            name="ck_near_expiry_confirmations_source_canonical",
        ),
        CheckConstraint(
            "(source = 'opening_stock' AND purchase_order_id IS NULL) "
            "OR (source = 'purchase_receipt' AND purchase_order_id IS NOT NULL)",
            name="ck_near_expiry_confirmations_source_context",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_near_expiry_confirmations_quantity_positive",
        ),
        ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_near_expiry_confirmations_batch_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["purchase_order_id", "tenant_id"],
            ["purchase_orders.id", "purchase_orders.tenant_id"],
            name="fk_near_expiry_confirmations_purchase_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["confirmed_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_near_expiry_confirmations_user_context",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_near_expiry_confirmations_tenant_created_at",
            "tenant_id",
            "created_at",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source = Column(String(32), nullable=False)
    quantity = Column(Integer, nullable=False)
    expiry_date = Column(Date, nullable=False)
    batch_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)
    purchase_order_id = Column(Integer, nullable=True)
    confirmed_by_user_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


class StockCount(Base):
    __tablename__ = "stock_counts"
    __table_args__ = (
        CheckConstraint("count_type IN ('cycle', 'full')", name="ck_stock_counts_type_canonical"),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'applied', 'cancelled')",
            name="ck_stock_counts_status_canonical",
        ),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_stock_counts_idempotency_key_length",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64",
            name="ck_stock_counts_request_fingerprint_length",
        ),
        CheckConstraint(
            "(status = 'draft' AND submitted_at IS NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND submitted_by_user_id IS NULL "
            "AND applied_by_user_id IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status = 'submitted' AND submitted_at IS NOT NULL AND applied_at IS NULL "
            "AND cancelled_at IS NULL AND submitted_by_user_id IS NOT NULL "
            "AND applied_by_user_id IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status = 'applied' AND submitted_at IS NOT NULL AND applied_at IS NOT NULL "
            "AND cancelled_at IS NULL AND submitted_by_user_id IS NOT NULL "
            "AND applied_by_user_id IS NOT NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status = 'cancelled' AND applied_at IS NULL AND applied_by_user_id IS NULL "
            "AND cancelled_at IS NOT NULL AND cancelled_by_user_id IS NOT NULL "
            "AND char_length(cancellation_reason) > 0 "
            "AND ((submitted_at IS NULL AND submitted_by_user_id IS NULL) OR "
            "(submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)))",
            name="ck_stock_counts_status_context",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_stock_counts_id_tenant_id"),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_stock_counts_tenant_idempotency_key"
        ),
        ForeignKeyConstraint(
            ["created_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_counts_creator_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["submitted_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_counts_submitter_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["applied_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_counts_applier_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cancelled_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_counts_canceller_same_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_stock_counts_tenant_status_created", "tenant_id", "status", "created_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    count_type = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="draft", server_default="draft")
    notes = Column(String(500), nullable=True)
    idempotency_key = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    created_by_user_id = Column(Integer, nullable=False)
    submitted_by_user_id = Column(Integer, nullable=True)
    applied_by_user_id = Column(Integer, nullable=True)
    cancelled_by_user_id = Column(Integer, nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(String(500), nullable=True)
    items = relationship(
        "StockCountItem",
        back_populates="stock_count",
        cascade="save-update, merge",
        order_by="StockCountItem.id",
    )


class StockCountItem(Base):
    __tablename__ = "stock_count_items"
    __table_args__ = (
        CheckConstraint("expected_quantity >= 0", name="ck_stock_count_items_expected_nonnegative"),
        CheckConstraint("counted_quantity >= 0", name="ck_stock_count_items_counted_nonnegative"),
        CheckConstraint(
            "(expected_quantity = counted_quantity AND variance_reason IS NULL) OR "
            "(expected_quantity <> counted_quantity AND variance_reason IN "
            "('counting_error', 'damage', 'loss', 'found_stock', 'data_correction', 'other'))",
            name="ck_stock_count_items_variance_context",
        ),
        UniqueConstraint("stock_count_id", "batch_id", name="uq_stock_count_items_count_batch"),
        UniqueConstraint(
            "id",
            "stock_count_id",
            "product_id",
            "batch_id",
            "tenant_id",
            name="uq_stock_count_items_source_context",
        ),
        ForeignKeyConstraint(
            ["stock_count_id", "tenant_id"],
            ["stock_counts.id", "stock_counts.tenant_id"],
            name="fk_stock_count_items_count_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_stock_count_items_batch_context",
            ondelete="RESTRICT",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    stock_count_id = Column(BigInteger, nullable=False)
    product_id = Column(Integer, nullable=False)
    batch_id = Column(Integer, nullable=False)
    expected_quantity = Column(Integer, nullable=False)
    counted_quantity = Column(Integer, nullable=False)
    variance_reason = Column(String(32), nullable=True)
    tenant_id = Column(Integer, nullable=False)
    stock_count = relationship("StockCount", back_populates="items")


class QuarantineDisposition(Base):
    __tablename__ = "quarantine_dispositions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('dispose', 'release')",
            name="ck_quarantine_dispositions_action_canonical",
        ),
        CheckConstraint("quantity > 0", name="ck_quarantine_dispositions_quantity_positive"),
        CheckConstraint(
            "char_length(reason) > 0", name="ck_quarantine_dispositions_reason_nonempty"
        ),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 64",
            name="ck_quarantine_dispositions_idempotency_key_length",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64",
            name="ck_quarantine_dispositions_request_fingerprint_length",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_quarantine_dispositions_tenant_idempotency_key",
        ),
        ForeignKeyConstraint(
            ["quarantine_item_id", "product_id", "tenant_id"],
            ["quarantine_items.id", "quarantine_items.product_id", "quarantine_items.tenant_id"],
            name="fk_quarantine_dispositions_item_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_quarantine_dispositions_batch_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["performed_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_quarantine_dispositions_actor_same_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_quarantine_dispositions_tenant_item_created",
            "tenant_id",
            "quarantine_item_id",
            "created_at",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    quarantine_item_id = Column(Integer, nullable=False)
    action = Column(String(32), nullable=False)
    quantity = Column(Integer, nullable=False)
    reason = Column(String(500), nullable=False)
    idempotency_key = Column(String(64), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    performed_by_user_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)
    batch_id = Column(Integer, nullable=False)
    tenant_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance', "
            "'opening_stock', 'purchase_receipt', 'sale', 'sales_return_restock', "
            "'sales_return_quarantine', 'purchase_return', 'stock_adjustment', "
            "'quarantine_transfer_out', 'quarantine_transfer_in', "
            "'quarantine_disposition', 'quarantine_release_out', 'quarantine_release_in')",
            name="ck_stock_movements_event_type_canonical",
        ),
        CheckConstraint(
            "stock_bucket IN ('on_hand', 'quarantine')",
            name="ck_stock_movements_bucket_canonical",
        ),
        CheckConstraint(
            "quantity_delta <> 0",
            name="ck_stock_movements_quantity_delta_nonzero",
        ),
        CheckConstraint(
            "char_length(source_key) > 0",
            name="ck_stock_movements_source_key_nonempty",
        ),
        CheckConstraint(
            "(stock_bucket = 'on_hand' AND batch_id IS NOT NULL) OR stock_bucket = 'quarantine'",
            name="ck_stock_movements_batch_context",
        ),
        CheckConstraint(
            "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance', "
            "'opening_stock', 'purchase_receipt', 'sales_return_restock', "
            "'sales_return_quarantine') AND quantity_delta > 0) "
            "OR (event_type IN ('sale', 'purchase_return', 'quarantine_transfer_out', "
            "'quarantine_disposition', 'quarantine_release_out') AND quantity_delta < 0) "
            "OR (event_type IN ('stock_adjustment') AND quantity_delta <> 0) "
            "OR (event_type IN ('quarantine_transfer_in', 'quarantine_release_in') "
            "AND quantity_delta > 0)",
            name="ck_stock_movements_direction_context",
        ),
        CheckConstraint(
            "(event_type IN ('migration_on_hand_balance', 'opening_stock', "
            "'purchase_receipt', 'sale', 'sales_return_restock', 'purchase_return', "
            "'stock_adjustment', 'quarantine_transfer_out', 'quarantine_release_in') "
            "AND stock_bucket = 'on_hand') "
            "OR (event_type IN ('migration_quarantine_balance', "
            "'sales_return_quarantine', 'quarantine_transfer_in', "
            "'quarantine_disposition', 'quarantine_release_out') "
            "AND stock_bucket = 'quarantine')",
            name="ck_stock_movements_event_bucket_context",
        ),
        CheckConstraint(
            "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance') "
            "AND source_type = 'migration') "
            "OR (event_type = 'opening_stock' AND source_type = 'product') "
            "OR (event_type = 'purchase_receipt' AND source_type = 'purchase_order') "
            "OR (event_type = 'sale' AND source_type = 'invoice') "
            "OR (event_type IN ('sales_return_restock', 'sales_return_quarantine') "
            "AND source_type = 'sales_return') "
            "OR (event_type = 'purchase_return' AND source_type = 'purchase_return') "
            "OR (event_type = 'stock_adjustment' AND source_type = 'stock_count') "
            "OR (event_type IN ('quarantine_transfer_out', 'quarantine_transfer_in') "
            "AND source_type = 'quarantine_item') "
            "OR (event_type IN ('quarantine_disposition', 'quarantine_release_out', "
            "'quarantine_release_in') AND source_type = 'quarantine_disposition')",
            name="ck_stock_movements_source_context",
        ),
        CheckConstraint(
            "(event_type IN ('migration_on_hand_balance', 'migration_quarantine_balance') "
            "AND actor_user_id IS NULL AND source_id IS NULL AND source_line_id IS NULL) "
            "OR (event_type NOT IN ('migration_on_hand_balance', "
            "'migration_quarantine_balance') AND actor_user_id IS NOT NULL "
            "AND source_id IS NOT NULL AND source_line_id IS NOT NULL)",
            name="ck_stock_movements_source_identity_required",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_key",
            name="uq_stock_movements_tenant_source_key",
        ),
        ForeignKeyConstraint(
            ["product_id", "tenant_id"],
            ["products.id", "products.tenant_id"],
            name="fk_stock_movements_product_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["batch_id", "product_id", "tenant_id"],
            ["product_batches.id", "product_batches.product_id", "product_batches.tenant_id"],
            name="fk_stock_movements_batch_context",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_stock_movements_actor_same_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_stock_movements_tenant_created_id",
            "tenant_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_stock_movements_tenant_product_created",
            "tenant_id",
            "product_id",
            "created_at",
        ),
        Index(
            "ix_stock_movements_tenant_batch",
            "tenant_id",
            "batch_id",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_type = Column(String(40), nullable=False)
    stock_bucket = Column(String(20), nullable=False)
    quantity_delta = Column(Integer, nullable=False)
    source_type = Column(String(40), nullable=False)
    source_id = Column(BigInteger, nullable=True)
    source_line_id = Column(BigInteger, nullable=True)
    source_key = Column(String(160), nullable=False)
    actor_user_id = Column(Integer, nullable=True)
    product_id = Column(Integer, nullable=False)
    batch_id = Column(Integer, nullable=True)
    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", name="fk_stock_movements_tenant", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
