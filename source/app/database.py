"""SQLAlchemy 基础设施（BM-V1-101）。

- Engine 连接时启用 foreign_keys=ON、WAL 与 busy_timeout；
- 每请求独立 Session：成功提交、异常回滚并关闭，不跨请求复用；
- SQLite 锁超时映射为稳定的可重试错误（503 database_busy）。
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import OperationalError as SqliteOperationalError

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings
from app.errors import AppError

logger = logging.getLogger("app.database")

BUSY_TIMEOUT_MS = 5000


def now_utc() -> datetime:
    """统一的时间源：naive UTC（SQLite 以字符串保存，避免带时区后缀破坏排序与比较）。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。仅通过 Alembic 建表，应用启动不调用 create_all()。"""


def _url_to_file_path(database_url: str) -> Path | None:
    """sqlite:/// 相对/绝对路径解析；:memory: 返回 None。"""
    if database_url.startswith("sqlite:///:memory:") or database_url.startswith(
        "sqlite+pysqlite:///:memory:"
    ):
        return None
    raw = database_url.removeprefix("sqlite:///").removeprefix("sqlite+pysqlite:///")
    return Path(raw)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """每次新建连接执行 SQLite 会话级 PRAGMA。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except SqliteOperationalError:  # :memory: 不支持 WAL
        pass
    cursor.close()


def _absolute_database_url(database_url: str) -> str:
    """相对 sqlite:/// 路径统一锚定到 APP_ROOT（与 ops 健康检查/备份一致）。"""
    if database_url.startswith("sqlite:///"):
        raw = database_url[len("sqlite:///") :]
        path = Path(raw)
        if not path.is_absolute():
            from app.config import APP_ROOT as root

            return f"sqlite:///{root / path}"
    return database_url


def create_engine_from_settings(settings: Settings) -> Engine:
    """按配置构建 Engine；文件库父目录不存在时自动创建。"""
    resolved_url = _absolute_database_url(settings.database_url)
    file_path = _url_to_file_path(resolved_url)
    if file_path is not None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        resolved_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def is_lock_timeout(exc: BaseException) -> bool:
    """判断异常是否为 SQLite 锁超时（可重试）。"""
    origin = getattr(exc, "orig", None)
    if isinstance(origin, SqliteOperationalError):
        text = str(origin).lower()
        return "locked" in text or "busy" in text
    return False


def map_database_error(exc: SQLAlchemyError) -> AppError:
    """把数据库异常映射为稳定错误；锁超时返回可重试错误，其余不再外泄堆栈。"""
    if is_lock_timeout(exc):
        logger.warning("database_busy retryable error: %s", exc)
        return AppError(503, "database_busy", "数据库正忙，请稍后重试。", retryable=True)
    logger.error("database_error: %s", exc)
    return AppError(500, "internal_error", "服务器内部错误，请稍后重试。")


def make_session_manager(engine: Engine):
    """构建请求级 Session 依赖工厂（供 create_app 使用）。"""
    session_factory = create_session_factory(engine)

    def get_session(request: Request) -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except AppError:
            session.rollback()
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise map_database_error(exc) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return get_session
