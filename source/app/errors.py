"""统一错误结构（BM-V1-004）。

所有业务错误使用稳定结构：

    {"error": {"code": "...", "message": "...", "field_errors": {...}}}

- 浏览器页面请求（Accept: text/html 或 HX-Request）渲染统一错误页/片段；
- /api 与 HTMX 请求返回同构 JSON/HTML 片段；
- 500 只记录脱敏服务端详情，浏览器不显示堆栈。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.error")

# 稳定错误码
CODE_INTERNAL = "internal_error"
CODE_BAD_REQUEST = "bad_request"
CODE_UNAUTHORIZED = "unauthorized"
CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_CONFLICT = "conflict"
CODE_VALIDATION = "validation_error"
CODE_PAYLOAD_TOO_LARGE = "payload_too_large"
CODE_TOO_MANY = "too_many_requests"
CODE_UNSUPPORTED = "unsupported"

_HTTP_MESSAGES: dict[int, tuple[str, str]] = {
    400: (CODE_BAD_REQUEST, "请求无法被处理。"),
    401: (CODE_UNAUTHORIZED, "请先登录。"),
    403: (CODE_FORBIDDEN, "没有权限执行该操作。"),
    404: (CODE_NOT_FOUND, "页面或资源不存在。"),
    405: (CODE_UNSUPPORTED, "请求方法不受支持。"),
    409: (CODE_CONFLICT, "数据已发生变化，请重新加载后再试。"),
    413: (CODE_PAYLOAD_TOO_LARGE, "上传内容超过大小限制。"),
    422: (CODE_VALIDATION, "输入校验未通过。"),
    429: (CODE_TOO_MANY, "请求过于频繁，请稍后再试。"),
    500: (CODE_INTERNAL, "服务器内部错误，请稍后重试。"),
}


class AppError(Exception):
    """可预期业务错误：携带 HTTP 状态、稳定 code、展示 message 与字段级错误。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field_errors: dict[str, str] | None = None,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors
        self.retryable = retryable  # 锁超时等可重试错误


def conflict(message: str, field_errors: dict[str, str] | None = None) -> AppError:
    return AppError(409, CODE_CONFLICT, message, field_errors, retryable=True)


def not_found(message: str = "资源不存在。") -> AppError:
    return AppError(404, CODE_NOT_FOUND, message)


def bad_request(message: str) -> AppError:
    return AppError(400, CODE_BAD_REQUEST, message)


def unauthorized(message: str = "请先登录。") -> AppError:
    return AppError(401, CODE_UNAUTHORIZED, message)


def forbidden(message: str = "没有权限执行该操作。") -> AppError:
    return AppError(403, CODE_FORBIDDEN, message)


def validation_error(field_errors: dict[str, str], message: str | None = None) -> AppError:
    return AppError(
        422,
        CODE_VALIDATION,
        message or "请检查表单中的错误项。",
        field_errors,
    )


def payload_too_large(message: str = "上传内容超过大小限制。") -> AppError:
    return AppError(413, CODE_PAYLOAD_TOO_LARGE, message)


def too_many_requests(message: str = "请求过于频繁，请稍后再试。") -> AppError:
    return AppError(429, CODE_TOO_MANY, message)


def _wants_html(request: Request) -> bool:
    """浏览器页面请求判定：HTMX 请求不返回整页（错误由前端处理），返回 JSON 结构。"""
    if request.headers.get("HX-Request") == "true":
        return False
    accept = request.headers.get("Accept", "")
    return "text/html" in accept


def _error_payload(
    code: str, message: str, field_errors: dict[str, str] | None, request_id: str | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if field_errors:
        payload["field_errors"] = field_errors
    if request_id:
        payload["request_id"] = request_id
    return payload


async def _render_error_page(
    request: Request, status_code: int, payload: dict[str, Any]
) -> Response:
    templates = request.app.state.templates
    context = {"request": request, "status_code": status_code, "error": payload}
    response: Response = templates.TemplateResponse(request, "error.html", context)
    response.status_code = status_code
    return response


def _json_error(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": payload})


async def _app_error_handler(request: Request, exc: AppError) -> Response:
    payload = _error_payload(
        exc.code,
        exc.message,
        exc.field_errors,
        getattr(request.state, "request_id", None),
    )
    if _wants_html(request):
        return await _render_error_page(request, exc.status_code, payload)
    return _json_error(exc.status_code, payload)


async def _validation_handler(request: Request, exc: RequestValidationError) -> Response:
    field_errors: dict[str, str] = {}
    for err in exc.errors():
        location = err.get("loc", ())

        # loc 形如 ("body","title") 或 ("query","page")
        field = ".".join(str(part) for part in location[1:]) or ".".join(
            str(part) for part in location
        )
        message = str(err.get("msg", "invalid value"))
        if field not in field_errors:
            field_errors[field] = message
    payload = _error_payload(CODE_VALIDATION, "输入校验未通过。", field_errors, None)
    if _wants_html(request):
        return await _render_error_page(request, 422, payload)
    return _json_error(422, payload)


async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
    code, message = _HTTP_MESSAGES.get(exc.status_code, (CODE_INTERNAL, "服务器内部错误。"))
    payload = _error_payload(code, message, None, getattr(request.state, "request_id", None))
    if _wants_html(request):
        return await _render_error_page(request, exc.status_code, payload)
    return _json_error(exc.status_code, payload)


async def _unhandled_handler(request: Request, exc: Exception) -> Response:
    # 仅记录脱敏服务端详情：方法、路径（不含 query）、错误类型与 message；绝不输出堆栈到浏览器
    logger.exception(
        "unhandled_error method=%s path=%s exc_type=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    payload = _error_payload(
        CODE_INTERNAL,
        "服务器内部错误，请稍后重试。",
        None,
        getattr(request.state, "request_id", None),
    )
    if _wants_html(request):
        response = await _render_error_page(request, 500, payload)
    else:
        response = _json_error(500, payload)
    return response


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
