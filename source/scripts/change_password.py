"""服务器交互式改密（BM-V1-205 账号恢复）。

遗忘密码时在服务器上执行；递增 session_version 使既有会话全部失效。
用法：
    python -m scripts.change_password
"""

from __future__ import annotations

import sys

from app.models.user import User
from app.services.auth_service import validate_new_password
from app.services.security import hash_password

from scripts._cli_common import open_cli_session, read_password_tty


def change_password_interactive() -> int:
    session = open_cli_session()
    try:
        user = session.query(User).first()
        if user is None:
            print(
                "数据库中还没有管理员账号，请先运行 python -m scripts.create_admin。",
                file=sys.stderr,
            )
            return 1
        password = read_password_tty("新密码（至少 12 个字符）：")
        password_again = read_password_tty("再次输入密码：")
        if password != password_again:
            print("两次输入不一致。", file=sys.stderr)
            return 2
        error_message = validate_new_password(password)
        if error_message:
            print(error_message, file=sys.stderr)
            return 2
        user.password_hash = hash_password(password)
        user.session_version += 1
        session.commit()
        print(f"密码已更新；管理员 {user.username} 的全部既有会话已失效。")
        return 0
    finally:
        session.close()


def main() -> int:
    if not sys.stdin.isatty():
        print("拒绝非交互调用：密码不接受命令行明文参数。", file=sys.stderr)
        return 2
    return change_password_interactive()


if __name__ == "__main__":
    raise SystemExit(main())
