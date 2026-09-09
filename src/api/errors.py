import logging
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from src.api.contracts import REQUEST_ID_HEADER, ApiValidationIssue, is_v1_path

logger = logging.getLogger(__name__)

ERROR_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unavailable")


def _default_message(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Request failed"


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [
        {
            "type": error["type"],
            "loc": error["loc"],
            "msg": error["msg"],
        }
        for error in exc.errors()
    ]


def _v1_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if details is not None:
        error["details"] = jsonable_encoder(details)
    response_headers = dict(headers or {})
    response_headers.setdefault(REQUEST_ID_HEADER, request_id)
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=response_headers,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    headers = dict(exc.headers or {})
    if not is_v1_path(request.url.path):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": jsonable_encoder(exc.detail)},
            headers=headers,
        )

    message = exc.detail if isinstance(exc.detail, str) else _default_message(exc.status_code)
    details = None if isinstance(exc.detail, str) else exc.detail
    return _v1_error_response(
        request,
        status_code=exc.status_code,
        code=ERROR_CODES.get(exc.status_code, "http_error"),
        message=message,
        details=details,
        headers=headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    safe_errors = _safe_validation_errors(exc)
    if not is_v1_path(request.url.path):
        return JSONResponse(
            status_code=422,
            content={"detail": jsonable_encoder(safe_errors)},
        )

    issues = [
        ApiValidationIssue(
            field=".".join(str(part) for part in error["loc"]),
            message=error["msg"],
            code=error["type"],
        ).model_dump()
        for error in safe_errors
    ]
    return _v1_error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=issues,
    )


async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    logger.exception(
        "Unhandled API exception",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
        },
    )
    if not is_v1_path(request.url.path):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
            headers={REQUEST_ID_HEADER: request_id},
        )
    return _v1_error_response(
        request,
        status_code=500,
        code="internal_error",
        message="Internal Server Error",
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_exception_handler),
    )
    app.add_exception_handler(
        StarletteHTTPException,
        cast(ExceptionHandler, http_exception_handler),
    )
    app.add_exception_handler(Exception, unexpected_exception_handler)
