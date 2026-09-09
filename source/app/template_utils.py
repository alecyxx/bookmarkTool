"""模板渲染辅助。页面路由统一通过本函数渲染，保证注入一致上下文。"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.config import Settings

NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("search", "/", "搜索"),
    ("bookmarks", "/bookmarks", "书签"),
    ("categories", "/categories", "分类"),
    ("tags", "/tags", "标签"),
    ("import_export", "/import-export", "导入/导出"),
    ("trash", "/trash", "回收站"),
    ("settings", "/settings", "设置"),
)


def render(request: Request, template_name: str, context: dict | None = None) -> HTMLResponse:
    templates = request.app.state.templates
    settings: Settings = request.app.state.settings
    # CSRF：优先取中间件预生成的 token，否则读 Cookie（模板隐藏字段与请求头共用）
    csrf_token = getattr(request.state, "csrf_token", None)
    if csrf_token is None:
        csrf_token = request.cookies.get(settings.csrf_cookie_name)
    base: dict = {
        "request": request,
        "active_nav": "",
        "page_title": "",
        "nav_items": NAV_ITEMS,
        "csrf_token": csrf_token,
    }
    if context:
        base.update(context)
    return templates.TemplateResponse(request, template_name, base)
