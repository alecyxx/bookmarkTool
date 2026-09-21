"""安全中间件（BM-V1-203/206/207）。

- CsrfMiddleware：所有改变状态的请求（含登录、退出、CRUD、批量、导入执行）强制校验；
  普通表单使用隐藏字段，HTMX 请求使用请求头；缺失/错误/过期/跨会话均 403；
- SecurityHeadersMiddleware：CSP、X-Content-Type-Options、Referrer-Policy、frame 限制；
  CSP 在远程 favicon 关闭时不允许第三方图片（img-src 'self' data:）；
- UploadSizeMiddleware：multipart 上传按 Content-Length 预检，超限 413。

说明：中间件内抛出的业务错误不会经过 ExceptionMiddleware（用户中间件在其外层），
因此本模块自行调用错误渲染逻辑。
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings
from app.errors import (
    _error_payload,
    _render_error_page,
    _wants_html,
    forbidden,
    payload_too_large,
)
from app.services.session_service import CsrfService, SessionService

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CsrfMiddleware(BaseHTTPMiddleware):
    """全局 CSRF 防线：写请求必须同时满足 Cookie 与提交值一致且签名有效。"""

    def __init__(
        self, app, settings: Settings, session_service: SessionService, csrf_service: CsrfService
    ):
        super().__init__(app)
        self.settings = settings
        self.session_service = session_service
        self.csrf = csrf_service

    async def dispatch(self, request: Request, call_next):
        csrf_cookie = request.cookies.get(self.settings.csrf_cookie_name)
        pending: tuple[str, int] | None = None
        if request.method == "GET":
            version = self._session_version(request)
            if not csrf_cookie or not self.csrf.verify(csrf_cookie, version):
                # 首次访问或浏览器残留了过期/旧密钥 token 时重新签发：
                # 模板隐藏字段与响应 Cookie 使用同一值，保证刷新页面可以自愈。
                token = self.csrf.issue(version)
                request.state.csrf_token = token
                pending = (token, version)
        if request.method in _WRITE_METHODS and not request.url.path.startswith("/health"):
            header_token = request.headers.get("X-CSRF-Token")
            content_type = request.headers.get("Content-Type", "")
            is_form = (
                "application/x-www-form-urlencoded" in content_type
                or "multipart/form-data" in content_type
            )
            if header_token is not None:
                # HTMX / 原生 JS 写请求：中间件直接校验，不读取请求体（避免破坏表单解析）
                session_version = self._session_version(request)
                if (
                    not csrf_cookie
                    or header_token != csrf_cookie
                    or not self.csrf.verify(header_token, session_version)
                ):
                    return await self._render_error(
                        request, forbidden("请求校验未通过，请刷新页面后重试（CSRF）。")
                    )
            elif not is_form:
                # JSON/其它正文的写请求必须携带请求头；HTML 表单由 endpoint 校验隐藏字段
                return await self._render_error(
                    request, forbidden("请求校验未通过，请刷新页面后重试（CSRF）。")
                )
        response = await call_next(request)
        if pending is not None:
            self._set_csrf_cookie(response, pending[0], version=pending[1])
        return response

    def _session_version(self, request: Request) -> int:
        """当前会话载荷中的版本；无会话视为 0（登录页场景）。"""
        raw = request.cookies.get(self.settings.session_cookie_name)
        payload = self.session_service.load(raw) if raw else None
        return int(payload.get("sv", 0)) if payload else 0

    def _set_csrf_cookie(self, response: Response, token: str, *, version: int) -> None:
        response.set_cookie(
            key=self.settings.csrf_cookie_name,
            value=token,
            max_age=self.settings.csrf_token_ttl_seconds,
            path="/",
            secure=self.settings.session_cookie_secure,
            httponly=False,  # 需要被 app.js 读取放入 X-CSRF-Token 头
            samesite=self.settings.session_cookie_same_site,
        )

    async def _render_error(self, request: Request, exc) -> Response:
        payload = _error_payload(
            exc.code, exc.message, exc.field_errors, getattr(request.state, "request_id", None)
        )
        if _wants_html(request):
            return await _render_error_page(request, exc.status_code, payload)
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content={"error": payload})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头（BM-V1-206）。网络搜索只跳转固定 HTTPS 目标，不放宽 frame-src。"""

    def __init__(self, app, settings: Settings):
        super().__init__(app)
        img_src = "img-src 'self' data:;"
        if settings.enable_remote_favicons:
            img_src = "img-src 'self' data: https:;"
        self.headers = {
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "connect-src 'self'; "
                f"{img_src} "
                "font-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "object-src 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        }
        if settings.app_env == "production":
            self.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in self.headers.items():
            response.headers.setdefault(name, value)
        return response


class UploadSizeMiddleware(BaseHTTPMiddleware):
    """上传预检（BM-V1-207）：multipart 请求超过配置上限直接 413，正文不会进入解析。"""

    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.max_upload_bytes = settings.max_upload_bytes

    async def dispatch(self, request: Request, call_next):
        if request.method in _WRITE_METHODS:
            content_type = request.headers.get("Content-Type", "")
            length_header = request.headers.get("Content-Length")
            if "multipart/form-data" in content_type and length_header:
                try:
                    if int(length_header) > self.max_upload_bytes:
                        return await self._reject(request)
                except ValueError:
                    return await self._reject(request)
        return await call_next(request)

    async def _reject(self, request: Request) -> Response:
        exc = payload_too_large("上传文件超过大小限制（默认 10 MiB）。")
        payload = _error_payload(
            exc.code, exc.message, None, getattr(request.state, "request_id", None)
        )
        if _wants_html(request):
            return await _render_error_page(request, 413, payload)
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=413, content={"error": payload})


def make_security_middleware(
    app,
    settings: Settings,
    session_service: SessionService,
    csrf_service: CsrfService,
) -> None:
    """挂载安全中间件；SecurityHeaders 位于 UploadSize 外层，覆盖提前返回。"""
    app.add_middleware(
        CsrfMiddleware,
        settings=settings,
        session_service=session_service,
        csrf_service=csrf_service,
    )
    app.add_middleware(UploadSizeMiddleware, settings=settings)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
