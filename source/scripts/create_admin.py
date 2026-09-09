"""创建管理员（BM-V1-201）。

- 从 TTY 两次读取密码，不接受命令行明文参数；
- 使用 Argon2id；
- 数据库已有管理员时拒绝创建第二个账号。

用法（首次部署，服务器上执行）：
    python -m scripts.create_admin
"""

from __future__ import annotations

import sys

from app.models.user import User
from app.services.auth_service import (
    PASSWORD_MIN_LENGTH,
    USERNAME_MAX_LENGTH,
    validate_new_password,
)
from app.services.normalize import is_valid_length, normalize_username, strip_display_name
from app.services.security import hash_password
from sqlalchemy import select

from scripts._cli_common import open_cli_session, read_password_tty


def create_admin(username: str, password: str, session=None) -> None:
    """核心逻辑（可测试）。校验规则与提示同 CLI。session 缺省时自动打开。"""
    if session is None:
        session = open_cli_session()
        auto_close = True
    else:
        auto_close = False
    try:
        if session.scalar(select(User.id).limit(1)) is not None:
            raise SystemExit("数据库中已存在管理员账号，拒绝创建第二个账号。")
        display = strip_display_name(username)
        if not is_valid_length(display, USERNAME_MAX_LENGTH):
            raise SystemExit(f"用户名长度必须在 1～{USERNAME_MAX_LENGTH} 字符之间。")
        normalized = normalize_username(display)
        if session.scalar(select(User).where(User.normalized_username == normalized)) is not None:
            raise SystemExit("规范化后的用户名已存在。")
        password_error = validate_new_password(password)
        if password_error:
            raise SystemExit(password_error)
        user = User(
            username=display,
            normalized_username=normalized,
            password_hash=hash_password(password),
        )
        session.add(user)
        session.commit()
        print(f"管理员 {display} 创建成功。")
    finally:
        if auto_close:
            session.close()


def main() -> int:
    if not sys.stdin.isatty():
        print("拒绝非交互调用：请在交互终端中运行，密码不接受命令行明文参数。", file=sys.stderr)
        return 2
    username = input("管理员用户名：").strip()
    if not username:
        print("用户名不能为空。", file=sys.stderr)
        return 2
    password = read_password_tty("密码（至少 12 个字符）：")
    password_again = read_password_tty("再次输入密码：")
    if password != password_again:
        print("两次输入不一致。", file=sys.stderr)
        return 2
    if len(password) < PASSWORD_MIN_LENGTH:
        print(f"密码长度至少 {PASSWORD_MIN_LENGTH} 个字符。", file=sys.stderr)
        return 2
    try:
        create_admin(username, password)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
