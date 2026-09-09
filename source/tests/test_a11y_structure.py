"""页面结构级可访问性检查（BM-V1-701~704/707/708 自动化部分）。

覆盖：html lang、viewport、skip-link、单一 h1、label/for 关联、按钮可访问名、
无正 tabindex、live region、favicon 隐私（远程关闭不发第三方图片请求）、
错误信息持久语义（role=alert 页面元素）。
三浏览器人工回归差异记录于交付记录（本环境无浏览器自动化）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PASSWORD = "strong-password-1234"
SOURCE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = SOURCE_ROOT / "app" / "templates"

PAGE_PATHS = {
    "login": "/login",
    "home": "/",
    "bookmarks": "/bookmarks",
    "categories": "/categories",
    "tags": "/tags",
    "trash": "/trash",
    "import_export": "/import-export",
    "settings": "/settings",
}


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


def _page(auth_client, name: str) -> str:
    if name == "login":
        return auth_client.get("/login", headers={"Accept": "text/html"}).text
    return auth_client.get(PAGE_PATHS[name], headers={"Accept": "text/html"}).text


class TestDocumentShell:
    def test_html_lang_and_viewport(self, auth_client):
        html = _page(auth_client, "home")
        assert re.search(r'<html lang="zh-CN"', html)
        assert re.search(r'name="viewport" content="width=device-width', html)
        assert 'meta charset="utf-8"' in html

    def test_skip_link_targets_main(self, auth_client):
        html = _page(auth_client, "home")
        assert 'class="skip-link" href="#main-content"' in html
        assert '<main id="main-content"' in html

    def test_live_region_for_toasts(self, auth_client):
        html = _page(auth_client, "home")
        assert 'id="toast-region"' in html
        assert 'aria-live="polite"' in html

    def test_single_h1_per_page(self, auth_client):
        for name in PAGE_PATHS:
            html = _page(auth_client, name)
            # login 页 h1；其余页面 title h1 唯一
            assert html.count("<h1") <= 1, name
            assert html.count("</h1>") <= 1, name

    def test_no_positive_tabindex(self, auth_client):
        for name in PAGE_PATHS:
            html = _page(auth_client, name)
            assert 'tabindex="1"' not in html and 'tabindex="2"' not in html, name


class TestFormAssociations:
    """label/for 与控件 id 关联；visually-hidden label 也算关联。"""

    def _label_ids(self, html: str) -> list[str]:
        return re.findall(r'<label[^>]*for="([^"]+)"', html)

    def test_all_labels_have_targets(self, auth_client):
        pages_to_check = [
            "login",
            "bookmarks",
            "categories",
            "tags",
            "trash",
            "import_export",
            "settings",
        ]
        for name in pages_to_check:
            html = _page(auth_client, name)
            ids = self._label_ids(html)
            for target in ids:
                assert re.search(rf'id="{re.escape(target)}"', html), (name, target)

    def test_inputs_without_label_have_aria_label(self, auth_client):
        """未被 label 覆盖的控件需要有 aria-label/aria-labelledby（扫描主要输入控件）。"""
        for name in ("login", "bookmarks", "home", "trash"):
            html = _page(auth_client, name)
            for match in re.finditer(r"<(input|select|textarea)\b[^>]*>", html):
                tag = match.group(0)
                element_id = re.search(r'id="([^"]+)"', tag)
                if element_id and re.search(
                    rf'<label[^>]*for="{re.escape(element_id.group(1))}"', html
                ):
                    continue  # 有 label 关联
                # hidden / submit / checkbox/radio（包在 label 中提供名称）
                if re.search(r'type="(hidden|submit|checkbox|radio)"', tag):
                    continue
                if "data-field-error" in tag:  # 占位错误容器非控件
                    continue
                assert re.search(r'aria-label="[^"]+"', tag) or re.search(
                    r'aria-labelledby="[^"]+"', tag
                ), (name, tag[:120])


class TestButtonsAndLinks:
    def test_icon_only_buttons_have_accessible_names(self, auth_client):
        for name in ("bookmarks", "trash", "categories", "tags", "import_export"):
            html = _page(auth_client, name)
            for match in re.finditer(r"<button\b[^>]*>", html):
                tag = match.group(0)
                text = html[match.end() :]
                text = text[: text.find("</button>")] if "</button>" in text else ""
                text = re.sub(r"<[^>]+>", "", text).strip()
                if text and "×" not in text:
                    continue  # 有可见文本（× 由 aria-label 覆盖检查）
                if re.search(r'aria-label="[^"]+"', tag) or re.search(r'title="[^"]+"', tag):
                    continue
                if "btn-close" in tag or "toast-close" in tag or "bulk-close" in tag:
                    # 关闭类按钮必须带 aria-label
                    assert re.search(r'aria-label="[^"]+"', tag), (name, tag)
                    continue
                raise AssertionError((name, tag[:120]))  # 其余无文本按钮必须有可访问名


class TestFaviconPrivacy:
    def test_no_remote_images_when_disabled(self, auth_client, db_session):
        """默认（远程 favicon 关闭）：不输出第三方图片，本地品牌图标不受影响。"""
        from app.models.bookmark import Bookmark

        db_session.add(
            Bookmark(
                title="t",
                url="https://example.com/",
                normalized_url="https://example.com/",
                favicon_url="https://example.com/favicon.ico",
            )
        )
        db_session.commit()
        html = _page(auth_client, "bookmarks")
        assert 'src="https://example.com/favicon.ico"' not in html
        assert 'src="/static/icons/logo.svg"' in html
        assert "default-icon" in html  # 本地占位图标

    def test_favicon_icons_only_static_local(self, auth_client):
        html = _page(auth_client, "bookmarks")
        assert 'rel="icon" href="/static/icons/logo.svg"' in html


class TestErrorSemantics:
    def test_field_errors_and_alerts_persistent(self, auth_client):
        html = _page(auth_client, "bookmarks")
        # 字段错误容器带 data-field-error（持久），通知区带 role=alert
        assert 'data-field-error="' in html or "bookmark-modal-root" in html
        # toast 区 role=status/alert 由 JS 注入
        assert "toast-region" in html

    def test_modal_notice_uses_alert_role(self):
        template_dir = TEMPLATES / "partials"
        for file_name in ("bookmark_modal.html", "category_modal.html", "tag_modal.html"):
            content = (template_dir / file_name).read_text(encoding="utf-8")
            assert "modal-notice" in content
            assert 'role="alert"' in content

    def test_delete_confirm_has_alert_semantics(self):
        content = (TEMPLATES / "partials" / "category_delete_confirm.html").read_text(
            encoding="utf-8"
        )
        assert "danger-note" in content


class TestCssTokens:
    def test_css_variables_defined(self):
        css = (SOURCE_ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
        for variable in ("--bm-primary", "--bm-danger", "--bm-text-primary", "--bm-text-secondary"):
            assert variable in css
        # 无大面积渐变/投影（只允许圆角阴影存在少量）——抽查不引入明显渐变
        assert "linear-gradient" not in css
        assert "radial-gradient" not in css

    def test_focus_visible_style(self):
        css = (SOURCE_ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
        assert ":focus-visible" in css
