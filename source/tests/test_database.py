"""SQLite 连接与会话管理测试（BM-V1-101）。

HTTP 层的完整提交/回滚行为在阶段 04 书签路由集成测试中覆盖；
此处验证连接 PRAGMA、锁超时映射与依赖管理器的提交/回滚语义。
"""

from __future__ import annotations

import sqlite3

import pytest
from app.database import is_lock_timeout, make_session_manager, map_database_error
from app.errors import AppError, conflict
from app.models.user import User
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


def test_pragmas_applied(db):
    """连接时启用外键、WAL 与 busy_timeout。"""
    with db.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_is_lock_timeout_detection():
    op_error = sqlite3.OperationalError("database is locked")
    wrapped = OperationalError("SELECT", {}, op_error)
    assert is_lock_timeout(wrapped)
    other = sqlite3.OperationalError("no such table: x")
    assert not is_lock_timeout(OperationalError("SELECT", {}, other))
    assert not is_lock_timeout(ValueError("nope"))


def test_map_database_error_retryable():
    op_error = sqlite3.OperationalError("database is locked")
    error = map_database_error(OperationalError("SELECT", {}, op_error))
    assert isinstance(error, AppError)
    assert error.status_code == 503
    assert error.code == "database_busy"
    assert error.retryable is True


def test_session_manager_commits_after_yield(db):
    """yield 正常结束 -> 提交并落库。"""
    manager = make_session_manager(db)
    generator = manager(None)  # request 参数未被依赖实现使用
    session = next(generator)
    session.add(User(username="ok", normalized_username="ok", password_hash="h"))
    with pytest.raises(StopIteration):
        next(generator)  # 恢复执行 -> commit
    with db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1


def test_session_manager_rolls_back_on_app_error(db):
    """yield 体内抛业务冲突 -> 回滚、无部分提交、异常原样抛出。"""
    manager = make_session_manager(db)
    generator = manager(None)
    session = next(generator)
    session.add(User(username="partial", normalized_username="partial", password_hash="h"))
    with pytest.raises(AppError) as excinfo:
        generator.throw(conflict("模拟冲突"))
    assert excinfo.value.status_code == 409
    with db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 0


def test_session_manager_not_reused_across_requests(db):
    """每个依赖调用都得到新 Session（不跨请求复用）。"""
    manager = make_session_manager(db)
    first = next(manager(None))
    second = next(manager(None))
    assert first is not second
    first.close()
    second.close()
