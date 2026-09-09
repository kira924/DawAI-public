from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# 1. Base Schema: Shared attributes
class TenantBase(BaseModel):
    name: str


# 2. Create Schema: What the user MUST send to register
class TenantCreate(TenantBase):
    # EmailStr automatically validates if the text contains @ and a valid domain
    owner_email: EmailStr

    model_config = ConfigDict(extra="forbid")


# Schema for updating a tenant. All fields are optional.
class TenantUpdate(BaseModel):
    name: Optional[str] = None
    subscription_plan: Optional[str] = None
    is_active: Optional[bool] = None

    model_config = ConfigDict(extra="forbid")


class TenantOwnershipUpdate(BaseModel):
    target_user_id: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


# 3. Response Schema: What the server returns AFTER saving to the database
class TenantResponse(TenantBase):
    id: int
    owner_email: EmailStr
    owner_user_id: Optional[int]
    subscription_plan: str
    is_active: bool
    created_at: datetime

    # Tell Pydantic to accept SQLAlchemy models and convert them to JSON automatically
    model_config = ConfigDict(from_attributes=True)


class TenantOwnershipTransferResponse(BaseModel):
    id: int
    tenant_id: int
    previous_owner_user_id: Optional[int]
    new_owner_user_id: int
    performed_by_user_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
