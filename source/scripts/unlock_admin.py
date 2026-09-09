"""解锁管理员（BM-V1-204）。

连续登录失败触发临时锁定后，由部署者在服务器上执行本命令解锁，
避免依赖重启清空状态。
用法：
    python -m scripts.unlock_admin
"""

from __future__ import annotations

import sys

from app.models.user import User
from sqlalchemy import select

from scripts._cli_common import open_cli_session


def unlock_admin(session=None) -> int:
    if session is None:
        session = open_cli_session()
        auto_close = True
    else:
        auto_close = False
    try:
        user = session.scalar(select(User).limit(1))
        if user is None:
            print("数据库中还没有管理员账号。", file=sys.stderr)
            return 1
        user.locked_until = None
        user.failed_login_count = 0
        session.commit()
        print(f"已解锁管理员 {user.username}（失败计数清零）。")
        return 0
    finally:
        if auto_close:
            session.close()


def main() -> int:
    return unlock_admin()


if __name__ == "__main__":
    raise SystemExit(main())
