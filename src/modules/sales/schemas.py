from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.money import Money, NonNegativeMoney


class PaymentType(str, Enum):
    CASH = "cash"
    CREDIT = "credit"


class ReturnCondition(str, Enum):
    SEALED = "sealed"
    OPENED_OR_DAMAGED = "opened_or_damaged"
    UNKNOWN = "unknown"


class PaidPortionDestination(str, Enum):
    CUSTOMER_CREDIT = "customer_credit"
    CASH = "cash"


class RecordedReturnCondition(str, Enum):
    SEALED = "sealed"
    OPENED_OR_DAMAGED = "opened_or_damaged"
    UNKNOWN = "unknown"
    LEGACY = "legacy"


class ReturnDisposition(str, Enum):
    RESTOCK = "restock"
    QUARANTINE = "quarantine"
    LEGACY = "legacy"


class InvoiceItemCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    sale_unit_price: NonNegativeMoney

    model_config = ConfigDict(extra="forbid")


class InvoiceItemResponse(BaseModel):
    id: int
    invoice_id: int
    product_id: int
    quantity: int
    list_unit_price: Money
    sale_unit_price: Money
    subtotal: Money

    model_config = ConfigDict(from_attributes=True)


class InvoiceCreate(BaseModel):
    idempotency_key: UUID = Field(default_factory=uuid4)
    payment_type: PaymentType = PaymentType.CASH
    customer_id: int | None = Field(default=None, gt=0)
    items: list[InvoiceItemCreate] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("payment_type", mode="before")
    @classmethod
    def normalize_payment_type(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_invoice(self) -> "InvoiceCreate":
        if self.payment_type == PaymentType.CREDIT and self.customer_id is None:
            raise ValueError("customer_id is required for credit sales")
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("duplicate products are not allowed in one invoice")
        return self


class InvoiceResponse(BaseModel):
    id: int
    idempotency_key: str
    total_amount: Money
    outstanding_amount: Money
    payment_type: PaymentType
    customer_id: int | None
    created_at: datetime
    user_id: int
    tenant_id: int
    items: list[InvoiceItemResponse]

    model_config = ConfigDict(from_attributes=True)


class SalesReturnItemCreate(BaseModel):
    invoice_item_id: int = Field(gt=0)
    batch_id: int | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0)
    condition: ReturnCondition

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_batch_for_restock(self) -> "SalesReturnItemCreate":
        if self.condition == ReturnCondition.SEALED and self.batch_id is None:
            raise ValueError("batch_id is required for sealed returns")
        return self


class SalesReturnCreate(BaseModel):
    invoice_id: int = Field(gt=0)
    idempotency_key: UUID
    paid_portion_destination: PaidPortionDestination = PaidPortionDestination.CUSTOMER_CREDIT
    items: list[SalesReturnItemCreate] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def reject_duplicate_return_lines(self) -> "SalesReturnCreate":
        identities = [(item.invoice_item_id, item.batch_id) for item in self.items]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate invoice item and batch pairs are not allowed")
        return self


class SalesReturnItemResponse(BaseModel):
    id: int
    invoice_item_id: int
    product_id: int
    batch_id: int | None
    quantity: int
    condition: RecordedReturnCondition
    disposition: ReturnDisposition
    unit_refund: Money
    refund_amount: Money

    model_config = ConfigDict(from_attributes=True)


class SalesReturnResponse(BaseModel):
    id: int
    invoice_id: int
    idempotency_key: str
    total_refund: Money
    cash_refund: Money
    debt_reduction: Money
    credit_balance_increase: Money
    shift_id: int
    user_id: int
    tenant_id: int
    created_at: datetime
    items: list[SalesReturnItemResponse]

    model_config = ConfigDict(from_attributes=True)
