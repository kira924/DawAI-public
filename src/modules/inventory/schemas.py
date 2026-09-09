from datetime import date, datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from src.core.business_time import (
    ExpiryStatus,
    classify_expiry,
    days_until_expiry,
    local_business_date,
    normalize_expiry_input,
)
from src.core.money import NonNegativeMoney


class ProductCategory(str, Enum):
    MEDICINE = "medicine"
    COSMETIC = "cosmetic"
    MEDICAL_SUPPLY = "medical_supply"
    BABY_CARE = "baby_care"
    OTHER = "other"


class ReturnPolicy(str, Enum):
    STANDARD = "standard"
    UNOPENED_ONLY = "unopened_only"
    NON_RETURNABLE = "non_returnable"


class StockCountType(str, Enum):
    CYCLE = "cycle"
    FULL = "full"


class StockCountStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPLIED = "applied"
    CANCELLED = "cancelled"


class StockVarianceReason(str, Enum):
    COUNTING_ERROR = "counting_error"
    DAMAGE = "damage"
    LOSS = "loss"
    FOUND_STOCK = "found_stock"
    DATA_CORRECTION = "data_correction"
    OTHER = "other"


class QuarantineCondition(str, Enum):
    SEALED = "sealed"
    OPENED_OR_DAMAGED = "opened_or_damaged"
    UNKNOWN = "unknown"


class QuarantineReason(str, Enum):
    EXPIRED_BATCH = "expired_batch"
    DAMAGE = "damage"
    RECALL = "recall"
    QUALITY_CONCERN = "quality_concern"
    OTHER = "other"


class QuarantineDispositionAction(str, Enum):
    DISPOSE = "dispose"
    RELEASE = "release"


class StockMovementEventType(str, Enum):
    MIGRATION_ON_HAND_BALANCE = "migration_on_hand_balance"
    MIGRATION_QUARANTINE_BALANCE = "migration_quarantine_balance"
    OPENING_STOCK = "opening_stock"
    PURCHASE_RECEIPT = "purchase_receipt"
    SALE = "sale"
    SALES_RETURN_RESTOCK = "sales_return_restock"
    SALES_RETURN_QUARANTINE = "sales_return_quarantine"
    PURCHASE_RETURN = "purchase_return"
    STOCK_ADJUSTMENT = "stock_adjustment"
    QUARANTINE_TRANSFER_OUT = "quarantine_transfer_out"
    QUARANTINE_TRANSFER_IN = "quarantine_transfer_in"
    QUARANTINE_DISPOSITION = "quarantine_disposition"
    QUARANTINE_RELEASE_OUT = "quarantine_release_out"
    QUARANTINE_RELEASE_IN = "quarantine_release_in"


class StockBucket(str, Enum):
    ON_HAND = "on_hand"
    QUARANTINE = "quarantine"


class StockMovementSourceType(str, Enum):
    MIGRATION = "migration"
    PRODUCT = "product"
    PURCHASE_ORDER = "purchase_order"
    INVOICE = "invoice"
    SALES_RETURN = "sales_return"
    PURCHASE_RETURN = "purchase_return"
    STOCK_COUNT = "stock_count"
    QUARANTINE_ITEM = "quarantine_item"
    QUARANTINE_DISPOSITION = "quarantine_disposition"


class QuarantineItemReason(str, Enum):
    EXPIRED_BATCH = "expired_batch"
    RETURN_CONDITION = "return_condition"
    DAMAGE = "damage"
    RECALL = "recall"
    QUALITY_CONCERN = "quality_concern"
    OTHER = "other"


class QuarantineItemStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"


class ReplenishmentRecommendationSource(str, Enum):
    MANUAL_POLICY = "manual_policy"


class ProductBase(BaseModel):
    name: str
    barcode: Optional[str] = None
    catalog_product_id: Optional[int] = Field(default=None, gt=0)
    category: ProductCategory = ProductCategory.MEDICINE
    price: NonNegativeMoney
    is_divisible: bool = False
    parts_per_unit: int = Field(default=1, gt=0)
    part_name: Optional[str] = None
    part_price: Optional[NonNegativeMoney] = None
    expiry_date: Optional[date] = Field(
        default=None,
        description="Expiry date as YYYY-MM-DD; YYYY-MM is normalized to month-end.",
    )
    return_policy: ReturnPolicy = ReturnPolicy.STANDARD

    @field_validator("expiry_date", mode="before")
    @classmethod
    def normalize_expiry_date(cls, value: object) -> object:
        return normalize_expiry_input(value)


class InitialBatchCreate(BaseModel):
    batch_number: str = Field(min_length=1)
    expiry_date: date = Field(
        description="Expiry date as YYYY-MM-DD; YYYY-MM is normalized to month-end."
    )

    @field_validator("expiry_date", mode="before")
    @classmethod
    def normalize_expiry_date(cls, value: object) -> object:
        return normalize_expiry_input(value)

    model_config = ConfigDict(extra="forbid")


class ProductCreate(ProductBase):
    initial_boxes: int = Field(default=0, ge=0)
    initial_parts: int = Field(default=0, ge=0)
    initial_batch: InitialBatchCreate | None = None
    confirm_near_expiry: bool = False

    @model_validator(mode="after")
    def require_batch_for_opening_stock(self) -> "ProductCreate":
        parts_per_unit = self.parts_per_unit if self.is_divisible and self.parts_per_unit > 0 else 1
        opening_quantity = (self.initial_boxes * parts_per_unit) + self.initial_parts
        if opening_quantity > 0 and self.initial_batch is None:
            raise ValueError("initial_batch is required when opening stock is greater than zero")
        if opening_quantity == 0 and self.initial_batch is not None:
            raise ValueError("initial_batch requires opening stock greater than zero")
        if opening_quantity == 0 and self.confirm_near_expiry:
            raise ValueError("confirm_near_expiry requires opening stock greater than zero")
        return self

    model_config = ConfigDict(extra="forbid")


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    barcode: Optional[str] = None
    catalog_product_id: Optional[int] = Field(default=None, gt=0)
    category: Optional[ProductCategory] = None
    price: Optional[NonNegativeMoney] = None
    is_divisible: Optional[bool] = None
    parts_per_unit: Optional[int] = Field(default=None, gt=0)
    part_name: Optional[str] = None
    part_price: Optional[NonNegativeMoney] = None
    expiry_date: Optional[date] = Field(
        default=None,
        description="Expiry date as YYYY-MM-DD; YYYY-MM is normalized to month-end.",
    )
    return_policy: Optional[ReturnPolicy] = None
    # We do not update stock here directly. Stock should be updated via Sales/Purchases.

    @field_validator("expiry_date", mode="before")
    @classmethod
    def normalize_expiry_date(cls, value: object) -> object:
        return normalize_expiry_input(value)

    model_config = ConfigDict(extra="forbid")


class ProductResponse(ProductBase):
    id: int
    tenant_id: int
    total_parts: int
    reorder_point_parts: int | None = None
    target_stock_parts: int | None = None
    sellable_parts: int = 0
    expired_parts: int = 0
    quarantined_parts: int = 0

    @computed_field
    def tracked_physical_parts(self) -> int:
        return self.total_parts + self.quarantined_parts

    # Magic of Pydantic: Compute available boxes on the fly
    @computed_field
    def available_boxes(self) -> int:
        if not self.is_divisible or self.parts_per_unit <= 1:
            return self.sellable_parts
        return self.sellable_parts // self.parts_per_unit

    # Magic of Pydantic: Compute remaining strips/parts on the fly
    @computed_field
    def available_parts(self) -> int:
        if not self.is_divisible or self.parts_per_unit <= 1:
            return 0
        return self.sellable_parts % self.parts_per_unit

    model_config = ConfigDict(from_attributes=True)


class ReplenishmentPolicyUpsert(BaseModel):
    reorder_point_parts: int | None
    target_stock_parts: int | None

    @model_validator(mode="after")
    def validate_policy(self) -> "ReplenishmentPolicyUpsert":
        if (self.reorder_point_parts is None) != (self.target_stock_parts is None):
            raise ValueError("reorder_point_parts and target_stock_parts must be set together")
        if self.reorder_point_parts is not None:
            if self.reorder_point_parts < 0:
                raise ValueError("reorder_point_parts must be non-negative")
            if (
                self.target_stock_parts is None
                or self.target_stock_parts <= self.reorder_point_parts
            ):
                raise ValueError("target_stock_parts must be greater than reorder_point_parts")
        return self

    model_config = ConfigDict(extra="forbid")


class ReplenishmentPolicyResponse(BaseModel):
    product_id: int
    tenant_id: int
    reorder_point_parts: int | None
    target_stock_parts: int | None
    is_configured: bool


class ReplenishmentRecommendationResponse(BaseModel):
    product_id: int
    product_name: str
    barcode: str | None
    parts_per_unit: int
    sellable_parts: int
    reorder_point_parts: int
    target_stock_parts: int
    suggested_order_parts: int
    suggested_full_units: int
    suggested_loose_parts: int
    recommendation_source: ReplenishmentRecommendationSource
    inventory_date: date


class ProductBatchResponse(BaseModel):
    id: int
    batch_number: str
    expiry_date: date
    quantity: int
    product_id: int
    tenant_id: int
    days_until_expiry: int = 0
    expiry_status: ExpiryStatus = ExpiryStatus.VALID
    is_sellable: bool = False

    @model_validator(mode="after")
    def attach_expiry_state(self) -> "ProductBatchResponse":
        today = local_business_date()
        self.days_until_expiry = days_until_expiry(self.expiry_date, as_of=today)
        self.expiry_status = classify_expiry(self.expiry_date, as_of=today)
        self.is_sellable = self.quantity > 0 and self.days_until_expiry >= 0
        return self

    model_config = ConfigDict(from_attributes=True)


class StockMovementResponse(BaseModel):
    id: int
    event_type: StockMovementEventType
    stock_bucket: StockBucket
    quantity_delta: int
    source_type: StockMovementSourceType
    source_id: int | None
    source_line_id: int | None
    source_key: str
    actor_user_id: int | None
    product_id: int
    batch_id: int | None
    tenant_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StockCountItemInput(BaseModel):
    batch_id: int = Field(gt=0)
    counted_quantity: int = Field(ge=0)
    variance_reason: StockVarianceReason | None = None

    model_config = ConfigDict(extra="forbid")


class StockCountCreate(BaseModel):
    idempotency_key: UUID = Field(default_factory=uuid4)
    count_type: StockCountType = StockCountType.CYCLE
    notes: str | None = Field(default=None, max_length=500)
    items: list[StockCountItemInput] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def reject_duplicate_batches(self) -> "StockCountCreate":
        batch_ids = [item.batch_id for item in self.items]
        if len(batch_ids) != len(set(batch_ids)):
            raise ValueError("duplicate batches are not allowed in one stock count")
        return self


class StockCountUpdate(BaseModel):
    notes: str | None = Field(default=None, max_length=500)
    items: list[StockCountItemInput] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def reject_duplicate_batches(self) -> "StockCountUpdate":
        batch_ids = [item.batch_id for item in self.items]
        if len(batch_ids) != len(set(batch_ids)):
            raise ValueError("duplicate batches are not allowed in one stock count")
        return self


class StockCountCancel(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid")


class StockCountItemResponse(BaseModel):
    id: int
    product_id: int
    batch_id: int
    expected_quantity: int
    counted_quantity: int
    variance_reason: StockVarianceReason | None

    @computed_field
    def variance(self) -> int:
        return self.counted_quantity - self.expected_quantity

    model_config = ConfigDict(from_attributes=True)


class StockCountResponse(BaseModel):
    id: int
    count_type: StockCountType
    status: StockCountStatus
    notes: str | None
    idempotency_key: str
    created_by_user_id: int
    submitted_by_user_id: int | None
    applied_by_user_id: int | None
    cancelled_by_user_id: int | None
    tenant_id: int
    created_at: datetime
    submitted_at: datetime | None
    applied_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    items: list[StockCountItemResponse]

    model_config = ConfigDict(from_attributes=True)


class QuarantineCreate(BaseModel):
    idempotency_key: UUID = Field(default_factory=uuid4)
    batch_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    condition: QuarantineCondition
    reason: QuarantineReason

    model_config = ConfigDict(extra="forbid")


class QuarantineItemResponse(BaseModel):
    id: int
    sales_return_item_id: int | None
    product_id: int
    batch_id: int | None
    quantity: int
    initial_quantity: int
    condition: QuarantineCondition
    reason: QuarantineItemReason
    status: QuarantineItemStatus
    idempotency_key: str
    created_by_user_id: int
    tenant_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QuarantineDispositionCreate(BaseModel):
    idempotency_key: UUID = Field(default_factory=uuid4)
    action: QuarantineDispositionAction
    quantity: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid")


class QuarantineDispositionResponse(BaseModel):
    id: int
    quarantine_item_id: int
    action: QuarantineDispositionAction
    quantity: int
    reason: str
    idempotency_key: str
    performed_by_user_id: int
    product_id: int
    batch_id: int
    tenant_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StockReconciliationIssueResponse(BaseModel):
    issue_type: str
    product_id: int
    batch_id: int | None
    stored_quantity: int
    ledger_quantity: int

    model_config = ConfigDict(from_attributes=True)


class StockReconciliationResponse(BaseModel):
    checked_products: int
    checked_batches: int
    issue_count: int
    issues_truncated: bool
    issues: list[StockReconciliationIssueResponse]

    model_config = ConfigDict(from_attributes=True)
