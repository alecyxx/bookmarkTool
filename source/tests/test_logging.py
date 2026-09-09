"""日志测试（BM-V1-004）：request_id、脱敏与格式。"""

from __future__ import annotations

import json
import logging

from app.logging_setup import JsonFormatter, TextFormatter, get_request_id, request_id_var


def test_request_id_present_in_access_log(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        response = client.get("/health/live")
    request_id = response.headers.get("X-Request-ID")
    assert request_id
    messages = [r.getMessage() for r in caplog.records if r.name == "app.access"]
    assert any("request_completed" in m and "status=200" in m for m in messages)
    # 访问日志不含 query（搜索词不进入业务日志的设计基础）
    assert all("path=/" in m for m in messages)


def test_logs_never_contain_sensitive_form_data(client, caplog):
    """日志不记录密码、Cookie、CSRF Token、上传正文。"""
    with caplog.at_level(logging.INFO):
        client.post(
            "/health/live",  # 405 路径也经过访问日志
            data={"password": "hunter2secret", "csrf_token": "tok-123"},
            headers={"Cookie": "bookmark_session=evil-session"},
        )
    assert "hunter2secret" not in caplog.text
    assert "tok-123" not in caplog.text
    assert "evil-session" not in caplog.text


def test_json_formatter_output_shape():
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app.formatter-test")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    token = request_id_var.set("req-abc")
    try:
        logger.info("hello %s", "world")
    finally:
        request_id_var.reset(token)
    record_line = stream.getvalue().strip()
    payload = json.loads(record_line)
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-abc"
    assert payload["message"] == "hello world"
    assert payload["logger"] == "app.formatter-test"


def test_text_formatter_contains_request_id():
    import io
    import logging as _logging

    stream = io.StringIO()
    handler = _logging.StreamHandler(stream)
    handler.setFormatter(TextFormatter())
    record = _logging.LogRecord("app.x", _logging.INFO, __file__, 1, "msg %s", ("a",), None)
    record.request_id = "req-xyz"
    handler.emit(record)
    assert "request_id=req-xyz" in stream.getvalue()


def test_contextvar_default_none():
    assert get_request_id() is None
