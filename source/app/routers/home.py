"""首页路由（BM-V1-001 骨架 / BM-V1-709 正式搜索首页）。

认证后的根页面为网络搜索首页；未登录访问时由守卫 303 跳转登录页。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.dependencies import get_current_user, require_page_redirect
from app.models.user import User
from app.template_utils import render

router = APIRouter()


@router.get("/", response_class=HTMLResponse, response_model=None, include_in_schema=False)
async def home_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    """认证后的网络搜索首页；未登录跳转登录页。"""
    if user is None:
        return require_page_redirect(request)
    settings = request.app.state.settings
    return render(
        request,
        "home.html",
        {
            "active_nav": "search",
            "page_title": "搜索",
            "default_engine": settings.default_web_search_engine,
        },
    )


@router.get("/robots.txt", include_in_schema=False)
async def robots_txt() -> str:
    """个人工具站点，拒绝全部抓取。"""
    return "User-agent: *\nDisallow: /\n"
