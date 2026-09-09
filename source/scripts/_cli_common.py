"""CLI 公共设施：数据库会话与 TTY 密码读取。"""

from __future__ import annotations

import getpass
import sys

from app.config import load_settings
from app.database import create_engine_from_settings, create_session_factory
from sqlalchemy.orm import Session, sessionmaker


def open_cli_session() -> Session:
    """运维脚本专用 Session：迁移场景不要求 SESSION_SECRET。"""
    settings = load_settings(require_session_secret=False)
    engine = create_engine_from_settings(settings)
    factory: sessionmaker[Session] = create_session_factory(engine)
    return factory()


def read_password_tty(prompt: str) -> str:
    """从 TTY 安全读取密码；非交互环境拒绝（不接收命令行明文）。"""
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。", file=sys.stderr)
        raise SystemExit(130) from None


def confirm_interactive() -> bool:
    try:
        answer = input("确认继续？(y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "y"
