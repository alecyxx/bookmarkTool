"""Alembic 迁移环境。

- 数据库 URL 来自应用配置（DATABASE_URL 环境变量/.env），保持单一口径；
- 迁移场景不要求 SESSION_SECRET（require_session_secret=False）；
- 使用 batch 模式以支持 SQLite 的 ALTER 场景（render_as_batch=True）；
- target_metadata 来自 app.database.Base（导入 app.models 完成全部注册）。
"""
from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import load_settings
from app.database import Base
import app.models  # noqa: F401 - 注册全部模型到 metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 数据库 URL 优先级：外部显式注入（如测试/发布脚本 set_main_option）> 应用配置。
# 应用配置加载不要求 SESSION_SECRET（迁移在密钥初始化前也可能执行）。
configured_url = config.get_main_option("sqlalchemy.url")
if configured_url:
    database_url = configured_url
else:
    settings = load_settings(require_session_secret=False)
    database_url = settings.database_url
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
