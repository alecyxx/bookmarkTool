"""FastAPI 依赖：请求级 Session、配置、当前管理员与会话守卫。

- 页面守卫：未登录 -> 303 跳转 /login?next=<当前站内路径>；
- API 守卫：未登录 -> 401 JSON（统一错误结构）；
- 已登录用户解析统一走 get_current_user（校验 DB 中 session_version 一致，
  改密后旧 Cookie 立即失效）。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import unauthorized
from app.models.user import User
from app.services.auth_service import next_query


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Generator[Session, None, None]:
    """每请求独立 Session：成功提交、异常回滚、始终关闭。"""
    session_manager = request.app.state.session_manager
    yield from session_manager(request)


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User | None:
    """解析会话 Cookie 中的管理员；会话无效/版本过期/用户缺失返回 None。"""
    if hasattr(request.state, "user_resolved"):
        return request.state.user
    settings: Settings = request.app.state.settings
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        request.state.user = None
        request.state.user_resolved = True
        return None
    payload = request.app.state.session_service.load(raw)
    if payload is None:
        request.state.user = None
        request.state.user_resolved = True
        return None
    user = db.get(User, int(payload["uid"]))
    if user is None or user.session_version != int(payload["sv"]):
        request.state.user = None
        request.state.user_resolved = True
        return None
    request.state.user = user
    request.state.user_resolved = True
    return user


def require_page_redirect(request: Request) -> RedirectResponse:
    """未登录页面访问的跳转目标（供页面 endpoint 使用）。"""
    target = f"/login?{next_query(request.url.path)}"
    return RedirectResponse(target, status_code=303)


def require_api_guard(request: Request, db: Annotated[Session, Depends(get_db)]):
    """/api 守卫：HTMX/页面请求未登录 -> 303 跳登录；普通 API 请求 -> 401 JSON。"""
    user = get_current_user(request, db)
    if user is not None:
        return user
    accept = request.headers.get("Accept", "")
    if request.headers.get("HX-Request") == "true" or "text/html" in accept:
        return require_page_redirect(request)
    raise unauthorized("请先登录。")


def require_api_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    """/api 路由守卫：未登录返回统一 401。"""
    user = get_current_user(request, db)
    if user is None:
        raise unauthorized("请先登录。")
    return user
