"""认证领域服务（BM-V1-201/202/204/205）。

- 登录：统一"用户名或密码错误"；账号不存在与密码错误不区分；
- 失败计数与临时锁定（阈值/时长来自 Settings）；
- 登录成功复位失败状态并记录 last_login_at；
- 站内 next 校验（开放重定向防线）；
- 会话/CSRF Cookie 的写入与清除。
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import now_utc
from app.errors import AppError, bad_request
from app.models.user import User
from app.services.normalize import normalize_username, strip_display_name
from app.services.security import hash_password, verify_password
from app.services.session_service import SessionService

PASSWORD_MIN_LENGTH = 12
USERNAME_MAX_LENGTH = 100

INVALID_CREDENTIALS_MESSAGE = "用户名或密码错误。"


def find_user_by_username(db: Session, username: str) -> User | None:
    normalized = normalize_username(strip_display_name(username))
    return db.scalar(select(User).where(User.normalized_username == normalized))


def is_account_locked(user: User, now=None) -> bool:
    now = now or now_utc()
    return user.locked_until is not None and user.locked_until > now


def record_failed_login(user: User, settings: Settings, now=None) -> None:
    """连续失败计数；达到阈值后设置锁定截止时间。"""
    now = now or now_utc()
    user.failed_login_count += 1
    if user.failed_login_count >= settings.login_max_attempts:
        user.locked_until = now + timedelta(seconds=settings.login_lock_seconds)


def login(
    db: Session, settings: Settings, username: str, password: str
) -> tuple[User | None, AppError | None]:
    """认证入口。成功时调用方负责提交（last_login_at/session 处理）。"""
    user = find_user_by_username(db, username)
    if user is not None and is_account_locked(user):
        return None, AppError(403, "account_locked", "尝试次数过多，账号已临时锁定，请稍后再试。")
    if user is None or not verify_password(password, user.password_hash):
        if user is not None:
            record_failed_login(user, settings)
        return None, AppError(400, "invalid_credentials", INVALID_CREDENTIALS_MESSAGE)
    # 成功：复位失败状态
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now_utc()
    return user, None


def validate_new_password(password: str) -> str | None:
    """返回错误消息或 None。单用户系统的口令策略：最小长度 + 非全同字符。"""
    if not password or len(password) < PASSWORD_MIN_LENGTH:
        return f"新密码长度至少 {PASSWORD_MIN_LENGTH} 个字符。"
    if len(set(password)) < 4:
        return "新密码过于简单，请增加字符多样性。"
    return None


def safe_next(target: str | None) -> str | None:
    """只允许站内相对路径；外部、协议相对和包含凭据的目标一律拒绝。"""
    if not target:
        return None
    target = target.strip()
    if len(target) > 512:
        return None
    if "://" in target or target.startswith("//") or "\\" in target:
        return None
    if not target.startswith("/"):
        return None
    return target


def next_query(target: str | None) -> str:
    return urlencode({"next": target}) if target else ""


def set_session_cookie(
    response: Response, settings: Settings, session_service: SessionService, value: str
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=value,
        max_age=settings.session_max_age_seconds,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_same_site,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def set_password(db: Session, user: User, new_password: str) -> int:
    """改密：写新哈希并递增 session_version（使既有会话全部失效），返回新版本号。"""
    user.password_hash = hash_password(new_password)
    user.session_version += 1
    return user.session_version


def require_current_password(db: Session, user: User, current_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise bad_request("当前密码不正确。")
