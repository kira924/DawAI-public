import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.contracts import (
    API_VERSION_HEADER,
    LEGACY_DEPRECATION_HEADER,
    REQUEST_ID_HEADER,
    is_legacy_api_path,
    is_v1_path,
)

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ApiContractMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        supplied_request_id = headers.get(REQUEST_ID_HEADER)
        request_id = (
            supplied_request_id
            if supplied_request_id and REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else str(uuid4())
        )
        scope.setdefault("state", {})["request_id"] = request_id
        path = scope.get("path", "")

        async def send_with_contract_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id
                if is_v1_path(path) or is_legacy_api_path(path):
                    response_headers[API_VERSION_HEADER] = "1"
                if is_legacy_api_path(path):
                    response_headers[LEGACY_DEPRECATION_HEADER] = "true"
            await send(message)

        await self.app(scope, receive, send_with_contract_headers)
