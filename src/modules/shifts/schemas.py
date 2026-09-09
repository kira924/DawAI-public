from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, computed_field

from src.core.business_time import local_business_date
from src.core.money import ZERO_MONEY, Money, NonNegativeMoney


class ShiftStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


# Schema for opening a new shift (requires only the starting cash)
class ShiftCreate(BaseModel):
    opening_balance: NonNegativeMoney = ZERO_MONEY

    model_config = ConfigDict(extra="forbid")


# Schema for closing a shift (requires the physical cash counted by the cashier)
class ShiftClose(BaseModel):
    actual_closing_balance: NonNegativeMoney

    model_config = ConfigDict(extra="forbid")


# Schema for returning shift data to the frontend
class ShiftResponse(BaseModel):
    id: int
    status: ShiftStatus
    start_time: datetime
    end_time: Optional[datetime] = None
    opening_balance: Money
    expected_closing_balance: Money
    actual_closing_balance: Optional[Money] = None
    difference: Optional[Money] = None
    user_id: int
    tenant_id: int

    @computed_field
    def opening_business_date(self) -> date:
        return local_business_date(self.start_time)

    @computed_field
    def closing_business_date(self) -> date | None:
        return local_business_date(self.end_time) if self.end_time is not None else None

    model_config = ConfigDict(from_attributes=True)
