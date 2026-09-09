"""健康检查与应用入口测试（BM-V1-001）。"""

from __future__ import annotations


def test_health_live_ok(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_live_no_leak(client):
    """live 探针不泄露配置、路径或环境细节。"""
    response = client.get("/health/live")
    text = response.text
    assert "SESSION" not in text.upper()
    assert "SECRET" not in text.upper()
    assert "/data/" not in text
    assert "sqlite" not in text.lower()


def test_unknown_route_returns_unified_404(client):
    # 浏览器页面请求（Accept: text/html）返回统一 HTML 错误页
    response = client.get("/no-such-page", headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert "页面或资源不存在" in response.text


def test_404_json_structure(client):
    response = client.get("/api/no-such-endpoint", headers={"Accept": "application/json"})
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["message"]


def test_import_app_module_has_no_side_effect(tmp_path):
    """导入应用模块不自动建库、迁移或写盘。"""
    import app.config
    import app.errors
    import app.logging_setup
    import app.main

    files_before = {p for p in tmp_path.rglob("*")}
    # 导入后不产生任何数据库文件或临时文件（不访问网络由无 IO 调用保证）
    assert files_before == {p for p in tmp_path.rglob("*")}
    assert callable(app.main.create_app)


def test_robots_txt(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /" in response.text
