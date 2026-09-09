"""基础页面壳测试（BM-V1-006）。

阶段 03 起页面需要登录：无状态页面断言使用公开登录页（渲染同一 base 壳）；
登录后首页的导航激活态断言位于 test_auth_flow.py。
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_NAV = [
    ("search", "/", "搜索"),
    ("bookmarks", "/bookmarks", "书签"),
    ("categories", "/categories", "分类"),
    ("tags", "/tags", "标签"),
    ("import_export", "/import-export", "导入/导出"),
    ("trash", "/trash", "回收站"),
    ("settings", "/settings", "设置"),
]


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "登录" in response.text


def test_nav_all_items_present(client):
    text = client.get("/login").text
    for _key, href, label in EXPECTED_NAV:
        assert f'href="{href}"' in text
        assert label in text


def test_static_assets_served(client):
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/js/page-init.js").status_code == 200
    assert client.get("/static/vendor/bootstrap/bootstrap.min.css").status_code == 200
    assert client.get("/static/vendor/bootstrap/bootstrap.bundle.min.js").status_code == 200
    assert client.get("/static/vendor/htmx/htmx.min.js").status_code == 200
    assert client.get("/static/icons/logo.svg").status_code == 200


def test_brand_uses_logo(client):
    text = client.get("/login").text
    assert "<title>登录 · 签栖</title>" in text
    assert 'rel="icon" href="/static/icons/logo.svg"' in text
    assert 'class="brand-logo" src="/static/icons/logo.svg" alt=""' in text
    assert 'aria-label="签栖首页"' in text
    assert "<span>签栖</span>" in text


def test_noscript_fallback_present(client):
    assert "noscript" in client.get("/login").text


def test_page_has_lang_and_viewport(client):
    text = client.get("/login").text
    assert 'lang="zh-CN"' in text
    assert 'name="viewport"' in text


def test_page_modules_use_csp_safe_external_initializer():
    base = (SOURCE_ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/page-init.js" defer></script>' in base

    templates = SOURCE_ROOT / "app" / "templates"
    inline_script = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
    for path in templates.rglob("*.html"):
        assert inline_script.search(path.read_text(encoding="utf-8")) is None, path

    initializer = (SOURCE_ROOT / "app" / "static" / "js" / "page-init.js").read_text(
        encoding="utf-8"
    )
    for module_name in (
        "WebSearch",
        "BookmarkUI",
        "CategoryUI",
        "TagUI",
        "ImportExportUI",
        "BulkUI",
    ):
        assert f'init("{module_name}"' in initializer
