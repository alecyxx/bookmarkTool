"""pytest 共享设施：隔离配置、迁移到 head 的文件库、独立 Session、HTTP 客户端。

规则（BM-V1-005/BM-V1-102）：
- 每个测试使用隔离数据库与独立配置；不写生产路径、不访问公网；
- 数据库 schema 通过真实 Alembic 迁移创建（验证迁移可用，不调用 create_all()）。
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from app.config import Settings, load_settings
from app.database import create_session_factory
from app.main import create_app
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

APP_ROOT = Path(__file__).resolve().parents[1]

BASE_TEST_ENV: dict[str, str] = {
    "APP_ENV": "testing",
    "DATABASE_URL": "sqlite:///:memory:",
    "SESSION_SECRET": "test-session-secret-0123456789abcdef0123456789abcdef",
    "ENABLE_REMOTE_FAVICONS": "false",
}


@pytest.fixture
def make_settings():
    """基于测试默认值 + 覆盖项构造隔离 Settings（不触碰真实环境变量）。"""

    def _factory(overrides: dict[str, str] | None = None) -> Settings:
        env = dict(BASE_TEST_ENV)
        if overrides:
            env.update(overrides)
        return load_settings(env=env, use_dotenv=False)

    return _factory


def _upgrade_to_head(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config as AlembicConfig

    config = AlembicConfig(str(APP_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest.fixture
def db(tmp_path) -> Generator[Engine, None, None]:
    """迁移到 head 的隔离文件库 Engine（WAL/外键 PRAGMA 在连接事件中生效）。"""
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    _upgrade_to_head(database_url)
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db) -> sessionmaker[Session]:
    return create_session_factory(db)


@pytest.fixture
def db_session(session_factory) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app(db, make_settings):
    """HTTP 应用：使用与 db fixture 相同的已迁移文件库。"""
    settings = make_settings({"DATABASE_URL": str(db.url)})
    return create_app(settings)


@pytest.fixture
def make_admin(db_session):
    """在隔离库创建管理员（与 app/client 同一数据库文件）。"""
    from app.models.user import User
    from app.services.normalize import normalize_username, strip_display_name
    from app.services.security import hash_password

    def _factory(username: str = "admin", password: str = "strong-password-1234") -> User:
        display = strip_display_name(username)
        user = User(
            username=display,
            normalized_username=normalize_username(display),
            password_hash=hash_password(password),
        )
        db_session.add(user)
        db_session.commit()
        return user

    return _factory


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    # 不跟随重定向：认证断言需要精确检查 303/302 与 Location
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as test_client:
        yield test_client
    # 释放应用级 Engine 连接（文件库 WAL 句柄）
    app.state.engine.dispose()
