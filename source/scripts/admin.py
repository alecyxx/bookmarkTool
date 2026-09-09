"""管理员运维 CLI（BM-V1-808：解锁、重置密码、账户列表）。

用法：
    python -m scripts.admin list
    python -m scripts.admin unlock <username>
    python -m scripts.admin reset-password <username> <new-password>

说明：只能以管理员系统身份在服务器/容器内执行（生产路径：
    docker compose exec app python -m scripts.admin unlock admin）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings
from app.database import create_engine_from_settings, create_session_factory
from app.models.user import User
from app.services.normalize import normalize_username
from app.services.security import hash_password
from sqlalchemy import select, update

logger = logging.getLogger("admin_tool")


def _session():
    settings = load_settings()
    engine = create_engine_from_settings(settings)
    return create_session_factory(engine)()


def cmd_list() -> None:
    with _session() as session:
        rows = session.execute(
            select(
                User.id,
                User.username,
                User.failed_login_count,
                User.locked_until,
                User.last_login_at,
            ).order_by(User.id)
        ).all()
        print(f"{'id':<4} {'username':<20} {'failed':<7} locked_until")
        for row in rows:
            print(f"{row.id:<4} {row.username:<20} {row.failed_login_count:<7} {row.locked_until}")


def cmd_unlock(username: str) -> None:
    normalized = normalize_username(username)
    with _session() as session:
        user = session.scalar(select(User).where(User.normalized_username == normalized))
        if user is None:
            print(f"NOT_FOUND {username}")
            raise SystemExit(1)
        user.failed_login_count = 0
        user.locked_until = None
        session.commit()
        print(f"UNLOCKED {user.username}")


def cmd_reset_password(username: str, new_password: str) -> None:
    from app.services.auth_service import validate_new_password

    error = validate_new_password(new_password)
    if error:
        print(f"INVALID_PASSWORD {error}")
        raise SystemExit(1)
    normalized = normalize_username(username)
    with _session() as session:
        user = session.scalar(select(User).where(User.normalized_username == normalized))
        if user is None:
            print(f"NOT_FOUND {username}")
            raise SystemExit(1)
        user.password_hash = hash_password(new_password)
        user.failed_login_count = 0
        user.locked_until = None
        # 密码变更使既有会话版本失效（与修改密码流程一致）
        session.execute(
            update(User).where(User.id == user.id).values(session_version=user.session_version + 1)
        )
        session.commit()
        print(f"PASSWORD_RESET {user.username}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bookmark Manager V1 管理员运维")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    unlock = subparsers.add_parser("unlock")
    unlock.add_argument("username")
    reset = subparsers.add_parser("reset-password")
    reset.add_argument("username")
    reset.add_argument("new_password")
    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "unlock":
        cmd_unlock(args.username)
    elif args.command == "reset-password":
        cmd_reset_password(args.username, args.new_password)


if __name__ == "__main__":
    main()
