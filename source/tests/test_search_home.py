"""网络搜索首页与隐私边界测试（BM-V1-709/710）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.main import create_app

PASSWORD = "strong-password-1234"
SOURCE_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def auth_client(client, make_admin):
    make_admin(password=PASSWORD)
    client.get("/login")
    csrf = client.cookies.get("bookmark_csrf", "")
    client.post(
        "/auth/login",
        data={"username": "admin", "password": PASSWORD, "csrf_token": csrf},
    )
    return client


class TestSearchHomePage:
    def test_redirects_to_login_when_anonymous(self, client):
        response = client.get("/")
        assert response.status_code == 303
        assert "/login" in response.headers["location"]

    def test_page_after_login(self, auth_client):
        response = auth_client.get("/", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert "聚合搜索" in response.text
        # 三个有文本名称的引擎选项 + 输入框 + 提交
        assert "Google" in response.text
        assert "Bing" in response.text
        assert "百度" in response.text
        assert 'id="web-search-input"' in response.text
        # 激活导航项是搜索
        assert 'class="nav-link active"' in response.text
        assert 'aria-current="page"' in response.text

    def test_nav_order_and_items(self, auth_client):
        response = auth_client.get("/", headers={"Accept": "text/html"})
        nav = response.text.split('<nav class="main-nav"')[1].split("</nav>")[0]
        labels = ["搜索", "书签", "分类", "标签", "导入/导出", "回收站", "设置"]
        positions = [nav.find(label) for label in labels]
        assert all(position >= 0 for position in positions)
        assert positions == sorted(positions)  # 顺序固定

    def test_default_engine_from_config(self, auth_client):
        response = auth_client.get("/", headers={"Accept": "text/html"})
        assert 'data-default-engine="google"' in response.text

    def test_shortcut_attributes(self, auth_client):
        home = auth_client.get("/", headers={"Accept": "text/html"})
        assert "data-shortcut-search" in home.text
        bookmarks = auth_client.get("/bookmarks", headers={"Accept": "text/html"})
        assert "data-shortcut-search" in bookmarks.text
        assert "data-shortcut-new" in bookmarks.text


class TestPrivacyBoundary:
    """710：搜索词不进入 FastAPI / 本站 URL / 日志；引擎映射固定。"""

    def test_no_backend_search_route(self):
        from app.config import load_settings

        app = create_app(
            load_settings(
                env={
                    "APP_ENV": "testing",
                    "SESSION_SECRET": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c",
                },
                use_dotenv=False,
            )
        )
        paths = [getattr(route, "path", "") for route in app.routes]
        assert "/search" not in paths  # 不建设服务端搜索端点
        assert all("/api/search" not in p for p in paths)

    def test_engine_whitelist_in_script(self):
        script = (SOURCE_ROOT / "app" / "static" / "js" / "web-search.js").read_text(
            encoding="utf-8"
        )
        assert "https://www.google.com/search" in script
        assert "https://www.bing.com/search" in script
        assert "https://www.baidu.com/s" in script
        # 参数名固定；不出现任意 URL redirect / 代理
        assert 'param: "q"' in script
        assert 'param: "wd"' in script
        assert "location.href" in script
        assert "searchParams.set" in script
        assert "fetch(" not in script  # 无后端请求
        assert "api key" not in script.lower()
        assert "iframe" not in script
        assert "500" in script  # 长度上限

    def test_search_form_has_no_backend_action(self):
        home = (SOURCE_ROOT / "app" / "templates" / "home.html").read_text(encoding="utf-8")
        # 表单没有本站 action：跳转完全由脚本构造（词不会发给本站）
        assert 'action="/' not in home

    def test_search_word_absent_from_static_assets(self):
        """模板与脚本中不存在真实可提交的搜索词样例与第三方密钥。"""
        combined = ""
        for path in [
            SOURCE_ROOT / "app" / "templates" / "home.html",
            SOURCE_ROOT / "app" / "static" / "js" / "web-search.js",
        ]:
            combined += path.read_text(encoding="utf-8")
        assert "googleapis" not in combined
        assert "apikey" not in combined.lower()

    def test_engine_radio_default_checked(self, auth_client):
        response = auth_client.get("/", headers={"Accept": "text/html"})
        assert 'name="engine" value="google" checked' in response.text


class TestShortcutsStatic:
    def test_ime_safety_in_shortcuts_script(self):
        script = (SOURCE_ROOT / "app" / "static" / "js" / "shortcuts.js").read_text(
            encoding="utf-8"
        )
        assert "isComposing" in script
        assert '"Process"' in script
        assert "data-shortcut-search" in script
        assert "data-shortcut-new" in script

    def test_shortcuts_loaded_in_base(self):
        base = (SOURCE_ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        assert "/static/js/shortcuts.js" in base
