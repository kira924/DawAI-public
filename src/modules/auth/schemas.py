from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.modules.users.schemas import UserRole

PasswordValue = str


class TokenType(str, Enum):
    BEARER = "bearer"


class SupportAuditEventType(str, Enum):
    GRANT_CREATED = "grant_created"
    GRANT_REVOKED = "grant_revoked"
    ACCESS_DENIED = "access_denied"
    REQUEST_STARTED = "request_started"


def _validate_password(password: str) -> str:
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain between 12 and 128 characters")
    return password


# This schema represents the final response when a user successfully logs in
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: TokenType
    expires_in: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)

    model_config = ConfigDict(extra="forbid")


class StatusMessage(BaseModel):
    detail: str


class SelfProfileResponse(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    tenant_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SelfProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid")

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name must not be blank")
        return normalized


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: PasswordValue

    model_config = ConfigDict(extra="forbid")

    _password_policy = field_validator("new_password")(_validate_password)


class PasswordResetRequest(BaseModel):
    email: EmailStr

    model_config = ConfigDict(extra="forbid")


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=512)
    new_password: PasswordValue

    model_config = ConfigDict(extra="forbid")

    _password_policy = field_validator("new_password")(_validate_password)


class EmailChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_email: EmailStr

    model_config = ConfigDict(extra="forbid")


class EmailChangeConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=512)

    model_config = ConfigDict(extra="forbid")


class SupportScope(str, Enum):
    READ_ONLY = "read_only"
    OPERATIONS = "operations"


class SupportGrantCreate(BaseModel):
    support_user_email: EmailStr
    scope: SupportScope = SupportScope.READ_ONLY
    reason: str = Field(min_length=10, max_length=1000)
    duration_minutes: int = Field(default=60, ge=5, le=60)

    model_config = ConfigDict(extra="forbid")


class SupportGrantResponse(BaseModel):
    id: str
    tenant_id: int
    support_user_id: int
    granted_by_user_id: int
    scope: SupportScope
    reason: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class SupportAuditResponse(BaseModel):
    id: int
    support_grant_id: str
    tenant_id: int
    support_user_id: int
    event_type: SupportAuditEventType
    request_method: str | None
    request_path: str | None
    detail: str | None
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)
