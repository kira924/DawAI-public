from datetime import date, datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.business_time import normalize_expiry_input
from src.core.money import ZERO_MONEY, Money, NonNegativeMoney, Percentage, PositiveMoney


# 1. Supplier Schemas
class PurchaseOrderStatus(str, Enum):
    PENDING = "PENDING"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class SupplierCreate(BaseModel):
    name: str
    contact_info: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SupplierResponse(SupplierCreate):
    id: int
    tenant_id: int

    model_config = ConfigDict(from_attributes=True)


# 3. Purchase Item Schemas (Details)
class PurchaseItemCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    base_cost: NonNegativeMoney
    discount_percentage: Percentage = Field(default=ZERO_MONEY)
    batch_number: str = Field(min_length=1, max_length=255)
    expiry_date: date = Field(
        description="Expiry date as YYYY-MM-DD; YYYY-MM is normalized to month-end."
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("batch_number")
    @classmethod
    def normalize_batch_number(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("batch_number must not be blank")
        return normalized

    @field_validator("expiry_date", mode="before")
    @classmethod
    def normalize_expiry_date(cls, value: object) -> object:
        return normalize_expiry_input(value)


class PurchaseItemResponse(BaseModel):
    id: int
    product_id: int
    received_batch_id: Optional[int]
    quantity: int
    base_cost: Money
    discount_percentage: Percentage
    discount_value: Money
    unit_cost: Money
    subtotal: Money
    # New fields for batch tracking
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None

    model_config = ConfigDict(from_attributes=True)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int = Field(gt=0)
    tax_percentage: Percentage = Field(default=ZERO_MONEY)
    items: List[PurchaseItemCreate] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("tax_percentage")
    @classmethod
    def enforce_tax_free_mvp(cls, value: Percentage) -> Percentage:
        if value != ZERO_MONEY:
            raise ValueError(
                "tax_percentage must remain zero until purchase tax policy is confirmed"
            )
        return value

    @model_validator(mode="after")
    def reject_duplicate_product_batches(self) -> "PurchaseOrderCreate":
        identities = [(item.product_id, item.batch_number) for item in self.items]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate product and batch pairs are not allowed")
        return self


class PurchaseOrderResponse(BaseModel):
    id: int
    status: PurchaseOrderStatus
    subtotal: Money
    total_discount: Money
    total_cost: Money
    created_at: datetime

    supplier_id: int
    user_id: int
    tenant_id: int

    # New Tax Fields
    tax_percentage: Percentage
    tax_value: Money

    items: List[PurchaseItemResponse]

    model_config = ConfigDict(from_attributes=True)


class PurchaseReceiptCreate(BaseModel):
    confirm_near_expiry: bool = False

    model_config = ConfigDict(extra="forbid")


# 4. Supplier Payment Schemas
class SupplierPaymentChannel(str, Enum):
    CASH = "cash"
    EXTERNAL = "external"


class SupplierPaymentCreate(BaseModel):
    supplier_id: int = Field(gt=0)
    amount: PositiveMoney
    idempotency_key: UUID
    payment_channel: SupplierPaymentChannel
    reference_number: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SupplierPaymentResponse(SupplierPaymentCreate):
    id: int
    payment_date: datetime
    user_id: int
    tenant_id: int
    shift_id: Optional[int]

    model_config = ConfigDict(from_attributes=True)


# 5. Purchase Return Schemas
class PurchaseReturnItemCreate(BaseModel):
    purchase_item_id: int = Field(gt=0)
    quantity: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class PurchaseReturnItemResponse(PurchaseReturnItemCreate):
    id: int
    return_id: int
    purchase_order_id: int
    product_id: int
    batch_id: int
    unit_refund: Money
    subtotal: Money

    model_config = ConfigDict(from_attributes=True)


class PurchaseReturnCreate(BaseModel):
    purchase_order_id: int = Field(gt=0)
    idempotency_key: UUID
    items: List[PurchaseReturnItemCreate] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def reject_duplicate_purchase_items(self) -> "PurchaseReturnCreate":
        item_ids = [item.purchase_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("duplicate purchase_item_id values are not allowed")
        return self


class PurchaseReturnResponse(BaseModel):
    id: int
    idempotency_key: str
    total_refund: Money
    created_at: datetime

    supplier_id: int
    purchase_order_id: Optional[int]
    user_id: int
    tenant_id: int

    items: List[PurchaseReturnItemResponse]

    model_config = ConfigDict(from_attributes=True)


class SupplierBalanceResponse(BaseModel):
    supplier_id: int
    supplier_name: str

    # Financial Aggregates
    total_purchases: Money
    total_payments: Money
    total_returns: Money

    # The final owed amount
    current_balance: Money

    model_config = ConfigDict(from_attributes=True)


class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    contact_info: Optional[str] = None

    model_config = ConfigDict(extra="forbid")
