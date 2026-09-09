"""错误边界测试（BM-V1-004）：状态码页面、错误结构与脱敏。"""

from __future__ import annotations

import pytest
from app.config import load_settings
from app.errors import AppError
from app.main import create_app
from fastapi import Request
from fastapi.responses import JSONResponse


@pytest.fixture
def error_client():
    """带测试专用炸裂路由的客户端，用于验证 500 路径。"""
    from fastapi.testclient import TestClient

    settings = load_settings(
        env={
            "APP_ENV": "testing",
            "SESSION_SECRET": "ab" * 20,
            "DATABASE_URL": "sqlite:///:memory:",
        },
        use_dotenv=False,
    )
    app = create_app(settings)

    @app.get("/_boom", include_in_schema=False)
    async def boom():
        raise RuntimeError("secret-token-should-not-leak")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_500_html_no_stack(error_client):
    response = error_client.get("/_boom")
    assert response.status_code == 500
    assert "服务器内部错误" in response.text
    assert "secret-token-should-not-leak" not in response.text
    assert "Traceback" not in response.text


def test_500_json_structure(error_client):
    response = error_client.get("/_boom", headers={"Accept": "application/json"})
    assert response.status_code == 500
    payload = response.json()
    assert payload["error"]["code"] == "internal_error"
    assert "Traceback" not in response.text


def test_500_logs_redacted_details(error_client, caplog):
    """500 记录脱敏服务端详情：错误类型可查；响应与日志不泄露请求侧敏感值。

    异常对象自身的 message 属于服务端详情（用于排查），允许出现在服务端日志；
    绝不允许出现在浏览器响应中（见 test_500_html_no_stack）。
    """
    import logging

    with caplog.at_level(logging.ERROR, logger="app.error"):
        response = error_client.get("/_boom", headers={"Cookie": "bookmark_session=leaky-cookie"})
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "unhandled_error" in joined
    assert "RuntimeError" in joined
    assert "leaky-cookie" not in caplog.text
    assert "secret-token-should-not-leak" not in response.text


def test_404_page(client):
    response = client.get("/definitely-missing", headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert "错误码" in response.text
    assert "not_found" in response.text


def test_405_method_not_allowed(client):
    response = client.post("/health/live", headers={"Accept": "text/html"})
    assert response.status_code == 405
    assert "请求方法不受支持" in response.text


def test_app_error_json_shape(client):
    """稳定错误结构含 code/message，可含 field_errors。"""

    def _raise_handler(request: Request) -> JSONResponse:
        raise AppError(409, "conflict", "数据已变化", {"version": "版本过期"})

    client.app.add_api_route("/_conflict", _raise_handler, methods=["GET"])
    response = client.get("/_conflict", headers={"Accept": "application/json"})
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["message"]
    assert body["error"]["field_errors"]["version"] == "版本过期"
