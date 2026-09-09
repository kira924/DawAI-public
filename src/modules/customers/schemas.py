from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from src.core.money import ZERO_MONEY, Money, NonNegativeMoney, PositiveMoney


# --- Customer Schemas ---
class CustomerCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    credit_limit: NonNegativeMoney = Field(default=ZERO_MONEY)

    model_config = ConfigDict(extra="forbid")


class CustomerResponse(BaseModel):
    id: int
    name: str
    phone: Optional[str]
    credit_limit: Money
    total_debt: Money
    credit_balance: Money
    created_at: datetime
    tenant_id: int

    model_config = ConfigDict(from_attributes=True)


# --- Payment Schemas ---
class CustomerPaymentCreate(BaseModel):
    idempotency_key: UUID = Field(default_factory=uuid4)
    customer_id: int = Field(gt=0)
    amount: PositiveMoney

    model_config = ConfigDict(extra="forbid")


class CustomerPaymentResponse(BaseModel):
    id: int
    idempotency_key: str
    amount: Money
    customer_id: int
    shift_id: int
    user_id: int
    tenant_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
