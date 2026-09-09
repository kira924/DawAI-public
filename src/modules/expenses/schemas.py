from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from src.core.money import Money, PositiveMoney


# Schema for taking money out of the drawer
class ExpenseCreate(BaseModel):
    idempotency_key: UUID = Field(default_factory=uuid4)
    amount: PositiveMoney
    description: str

    model_config = ConfigDict(extra="forbid")


# Schema for returning the expense record
class ExpenseResponse(BaseModel):
    id: int
    idempotency_key: str
    amount: Money
    description: str
    created_at: datetime
    shift_id: int
    user_id: int
    tenant_id: int

    model_config = ConfigDict(from_attributes=True)
