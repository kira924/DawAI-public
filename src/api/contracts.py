from typing import Any

from pydantic import BaseModel


class ApiValidationIssue(BaseModel):
    field: str
    message: str
    code: str


class ApiError(BaseModel):
    code: str
    message: str
    request_id: str
    details: Any | None = None


class ApiErrorResponse(BaseModel):
    error: ApiError


REQUEST_ID_HEADER = "X-Request-ID"
API_VERSION_HEADER = "X-API-Version"
API_V1_PREFIX = "/api/v1"
LEGACY_API_PREFIX = "/api"
LEGACY_DEPRECATION_HEADER = "Deprecation"

CONTRACT_RESPONSE_HEADERS = {
    REQUEST_ID_HEADER: {
        "description": "Correlation identifier for this request.",
        "schema": {"type": "string"},
    },
    API_VERSION_HEADER: {
        "description": "Major API contract version used for this response.",
        "schema": {"type": "string", "enum": ["1"]},
    },
}

API_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {
        "model": ApiErrorResponse,
        "description": "Bad request",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    401: {
        "model": ApiErrorResponse,
        "description": "Authentication required",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    403: {
        "model": ApiErrorResponse,
        "description": "Permission denied",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    404: {
        "model": ApiErrorResponse,
        "description": "Resource not found",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    405: {
        "model": ApiErrorResponse,
        "description": "Method not allowed",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    409: {
        "model": ApiErrorResponse,
        "description": "State conflict",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    422: {
        "model": ApiErrorResponse,
        "description": "Request validation failed",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    429: {
        "model": ApiErrorResponse,
        "description": "Rate limit exceeded",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    500: {
        "model": ApiErrorResponse,
        "description": "Unexpected server error",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
    503: {
        "model": ApiErrorResponse,
        "description": "Service unavailable",
        "headers": CONTRACT_RESPONSE_HEADERS,
    },
}


def is_v1_path(path: str) -> bool:
    return path == API_V1_PREFIX or path.startswith(f"{API_V1_PREFIX}/")


def is_legacy_api_path(path: str) -> bool:
    return (
        path == LEGACY_API_PREFIX or path.startswith(f"{LEGACY_API_PREFIX}/")
    ) and not is_v1_path(path)
