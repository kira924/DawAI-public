from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    MANAGER = "manager"
    PHARMACIST = "pharmacist"


# Base Schema: Attributes shared across multiple schemas
class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    role: UserRole = UserRole.PHARMACIST
    is_active: bool = True
    tenant_id: int


# Create Schema: What we expect from the frontend when creating a user
class UserCreate(UserBase):
    password: str = Field(min_length=12, max_length=128)

    model_config = ConfigDict(extra="forbid")


# Update Schema: For modifying user details later
class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None

    model_config = ConfigDict(extra="forbid")


# Response Schema: What we return to the frontend
class UserResponse(UserBase):
    id: int
    created_at: datetime

    # Notice we DO NOT include the password field here at all!

    model_config = ConfigDict(from_attributes=True)
