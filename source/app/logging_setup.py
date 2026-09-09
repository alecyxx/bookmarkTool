"""结构化日志（BM-V1-004）。

- text 格式面向本地开发；json 格式面向容器日志收集（production 默认）；
- 通过 contextvars 关联请求 ID，middleware 在每个请求内设置；
- 日志内容规则：允许记录方法、路径（不含 query）、状态、耗时与来源 IP；
  不得记录密码、Cookie、CSRF Token、上传正文或完整敏感表单。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


def _record_request_id(record: logging.LogRecord) -> str:
    """优先取 filter 注入的字段，缺失时回退 contextvar（未挂 filter 的 handler 也能关联）。"""
    value = getattr(record, "request_id", None)
    if value is None:
        value = request_id_var.get()
    return value or "-"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": _record_request_id(record),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.request_id = _record_request_id(record)
        base = "%(asctime)s %(levelname)-7s %(name)s request_id=%(request_id)s %(message)s"
        formatter = logging.Formatter(base, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


_installed_handler: logging.Handler | None = None


def setup_logging(level: str = "INFO", *, fmt: str = "text") -> None:
    """幂等配置根 logger：首次调用安装 handler，之后只调整级别，避免破坏测试捕获。"""
    logger = logging.getLogger()
    logger.setLevel(level.upper())
    global _installed_handler
    if _installed_handler is None or _installed_handler not in logger.handlers:
        if _installed_handler is not None:
            logger.removeHandler(_installed_handler)
        handler = logging.StreamHandler(sys.stdout)
        handler.addFilter(_RequestIdFilter())
        logger.addHandler(handler)
        _installed_handler = handler
        # 第三方库噪声控制
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
    if fmt == "json":
        _installed_handler.setFormatter(JsonFormatter())
    else:
        _installed_handler.setFormatter(TextFormatter())
