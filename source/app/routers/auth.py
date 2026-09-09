"""认证与设置路由（BM-V1-202/205）。

- GET /login：登录页（已登录跳转 /）；
- POST /auth/login：登录成功重签会话与 CSRF Cookie；站内相对 next 才允许返回；
- POST /auth/logout：清除会话与 CSRF Cookie；
- GET /settings：设置页（登录保护）；
- PUT /api/settings/profile、PUT /api/settings/password：用户名/密码修改。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.client_ip import client_ip
from app.config import Settings
from app.dependencies import (
    get_current_user,
    get_db,
    require_api_user,
    require_page_redirect,
)
from app.errors import validation_error
from app.models.user import User
from app.services import auth_service as auth
from app.services.normalize import normalize_username, strip_display_name
from app.template_utils import render

router = APIRouter()
security_logger = logging.getLogger("app.security")


def _set_csrf_cookie(
    response, request: Request, *, version: int, token: str | None = None
) -> str:
    settings: Settings = request.app.state.settings
    token = token or request.app.state.csrf_service.issue(version)
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        max_age=settings.csrf_token_ttl_seconds,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite=settings.session_cookie_same_site,
    )
    return token


def _request_session_version(request: Request) -> int:
    """返回当前签名会话携带的版本；无有效会话时使用登录前版本 0。"""
    settings: Settings = request.app.state.settings
    raw_session = request.cookies.get(settings.session_cookie_name)
    payload = request.app.state.session_service.load(raw_session) if raw_session else None
    return int(payload.get("sv", 0)) if payload else 0


def _form_csrf_valid(request: Request, submitted: str) -> bool:
    """同步 HTML 表单的 CSRF 校验：隐藏字段 == Cookie 值且签名/版本有效。"""
    settings: Settings = request.app.state.settings
    cookie = request.cookies.get(settings.csrf_cookie_name)
    if not cookie or submitted != cookie:
        return False
    version = _request_session_version(request)
    return request.app.state.csrf_service.verify(submitted, version)


# ---------- 页面 ----------


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
    next: str | None = None,
) -> HTMLResponse:
    if user is not None:
        return RedirectResponse(auth.safe_next(next) or "/", status_code=303)
    target = auth.safe_next(next) if next else None
    return render(
        request,
        "login.html",
        {"active_nav": "", "page_title": "登录", "next_target": target, "form_error": None},
    )


@router.post(
    "/auth/login", response_class=HTMLResponse, response_model=None, include_in_schema=False
)
async def login_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    csrf_token: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    settings: Settings = request.app.state.settings
    if not _form_csrf_valid(request, csrf_token):
        version = _request_session_version(request)
        fresh_csrf = request.app.state.csrf_service.issue(version)
        context = {
            "active_nav": "",
            "page_title": "登录",
            "next_target": auth.safe_next(next) if next else None,
            "form_error": "页面已过期，安全令牌已刷新，请重新提交。",
            "csrf_token": fresh_csrf,
        }
        response: HTMLResponse | RedirectResponse = render(request, "login.html", context)
        response.status_code = 403
        _set_csrf_cookie(response, request, version=version, token=fresh_csrf)
        return response
    user, error = auth.login(db, settings, username, password)
    if error is not None:
        # 提前提交失败计数/锁定状态（依赖退出时的再次 commit 为幂等 no-op）
        db.commit()
        security_logger.warning("login_failed code=%s ip=%s", error.code, client_ip(request))
        context = {
            "active_nav": "",
            "page_title": "登录",
            "next_target": auth.safe_next(next) if next else None,
            "form_error": error.message,
        }
        response: HTMLResponse | RedirectResponse = render(request, "login.html", context)
        response.status_code = error.status_code
        return response
    # 登录成功：提交并重签会话与 CSRF（绑定当前 session_version）
    db.commit()
    security_logger.info("login_success user_id=%s ip=%s", user.id, client_ip(request))
    session_value = request.app.state.session_service.issue(user.id, user.session_version)
    target = auth.safe_next(next) if next else None
    redirect = RedirectResponse(target or "/", status_code=303)
    auth.set_session_cookie(redirect, settings, request.app.state.session_service, session_value)
    _set_csrf_cookie(redirect, request, version=user.session_version)
    return redirect


@router.post("/auth/logout", response_model=None, include_in_schema=False)
async def logout(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    settings: Settings = request.app.state.settings
    if not _form_csrf_valid(request, csrf_token):
        response = render(
            request,
            "error.html",
            {
                "status_code": 403,
                "error": {
                    "code": "forbidden",
                    "message": "请求校验未通过，请刷新页面后重试（CSRF）。",
                },
            },
        )
        response.status_code = 403
        return response
    response: HTMLResponse | RedirectResponse = RedirectResponse("/login", status_code=303)
    auth.clear_auth_cookies(response, settings)
    return response


@router.get("/settings", response_class=HTMLResponse, response_model=None, include_in_schema=False)
async def settings_page(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user)],
) -> HTMLResponse:
    if user is None:
        return require_page_redirect(request)
    return render(
        request,
        "settings.html",
        {"active_nav": "settings", "page_title": "设置", "current_user": user},
    )


# ---------- 设置 API（JSON；HTMX/原生 JS 调用） ----------


class ProfileUpdate(BaseModel):
    username: str = Field(min_length=1, max_length=100)


class PasswordUpdate(BaseModel):
    current_password: str
    new_password: str


@router.put("/api/settings/profile")
async def update_profile(
    payload: ProfileUpdate,
    user: Annotated[User, Depends(require_api_user)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    display = strip_display_name(payload.username)
    if not 1 <= len(display) <= auth.USERNAME_MAX_LENGTH:
        raise validation_error(
            {"username": f"用户名长度必须在 1～{auth.USERNAME_MAX_LENGTH} 字符之间。"}
        )
    normalized = normalize_username(display)
    if normalized != user.normalized_username:
        existing = auth.find_user_by_username(db, display)
        if existing is not None and existing.id != user.id:
            raise validation_error({"username": "该用户名已被使用。"})
    user.username = display
    user.normalized_username = normalized
    db.commit()
    return JSONResponse({"ok": True, "message": "用户名已更新。"})


@router.put("/api/settings/password")
async def update_password(
    payload: PasswordUpdate,
    request: Request,
    user: Annotated[User, Depends(require_api_user)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    auth.require_current_password(db, user, payload.current_password)
    error_message = auth.validate_new_password(payload.new_password)
    if error_message:
        raise validation_error({"new_password": error_message})
    new_version = auth.set_password(db, user, payload.new_password)
    db.commit()
    # 重签本请求会话与 CSRF（新版本），旧会话全部失效
    session_value = request.app.state.session_service.issue(user.id, new_version)
    response = JSONResponse({"ok": True, "message": "密码已更新，其它登录会话已失效。"})
    auth.set_session_cookie(
        response, request.app.state.settings, request.app.state.session_service, session_value
    )
    _set_csrf_cookie(response, request, version=new_version)
    return response
