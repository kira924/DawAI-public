import os
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
ENV_FILE_PATH = os.path.join(ROOT_DIR, ".env")


class Settings(BaseSettings):
    # Application basic info
    PROJECT_NAME: str = "DawAI"
    VERSION: str = "1.0.0"

    DATABASE_URL: str

    SECRET_KEY: str = Field(min_length=32)
    ALGORITHM: Literal["HS256"] = "HS256"
    JWT_ISSUER: str = "dawai-api"
    JWT_AUDIENCE: str = "dawai-clients"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15, ge=5, le=60)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=30, ge=1, le=90)

    LOGIN_MAX_FAILURES: int = Field(default=5, ge=3, le=20)
    LOGIN_IP_MAX_FAILURES: int = Field(default=25, ge=5, le=100)
    LOGIN_WINDOW_MINUTES: int = Field(default=15, ge=1, le=60)
    LOGIN_BLOCK_MINUTES: int = Field(default=15, ge=1, le=1440)

    ACCOUNT_ACTION_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=5, le=120)
    PASSWORD_RESET_BASE_URL: str = "http://localhost:5173/reset-password"
    EMAIL_CHANGE_BASE_URL: str = "http://localhost:5173/confirm-email"

    SMTP_HOST: str | None = None
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    SMTP_USE_STARTTLS: bool = True

    SUPPORT_ACCESS_MAX_MINUTES: int = Field(default=60, ge=5, le=60)
    BUSINESS_TIMEZONE: str = "Africa/Cairo"

    @field_validator("BUSINESS_TIMEZONE")
    @classmethod
    def validate_business_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("BUSINESS_TIMEZONE must be a valid IANA timezone") from error
        return value

    # Read configuration from .env file
    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH, env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
