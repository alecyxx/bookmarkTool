"""HTTP 中间件（请求上下文、访问日志与安全响应头骨架）。"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.client_ip import client_ip
from app.logging_setup import request_id_var

logger = logging.getLogger("app.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 request_id（不回显客户端值，防日志注入），记录访问日志。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        # 必须在 call_next 前写入：安全中间件可能提前返回 403/413。
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException:
            request_id_var.reset(token)
            raise
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        # 路径不含 query：外部搜索词等查询内容不进入业务日志
        logger.info(
            "request_completed method=%s path=%s status=%s duration_ms=%s ip=%s",
            request.method,
            request.url.path,
            getattr(response, "status_code", 500),
            elapsed_ms,
            client_ip(request),
        )
        response.headers.setdefault("X-Request-ID", request_id)
        request_id_var.reset(token)
        return response
