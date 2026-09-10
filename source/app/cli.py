"""签栖单文件发行入口。

PEX SCIE 将本模块设为入口，使 VPS 只需一个 ``qianqi`` 可执行文件。
配置默认从 /etc/bookmark/bookmark.env 加载，已有进程环境变量优先。
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from pathlib import Path

from app import __version__
from app.config import APP_ROOT, _load_dotenv

VERSION = __version__
DEFAULT_ENV_FILE = Path("/etc/bookmark/bookmark.env")

HELP = """签栖 {version}

用法：qianqi <命令> [参数]

命令：
  serve             启动服务（127.0.0.1:8000，单 worker）
  migrate           将数据库迁移到当前版本
  create-admin      交互式创建管理员
  change-password   交互式重置管理员密码
  unlock-admin      解锁管理员
  backup            一致性备份
  cleanup           清理过期导入任务和临时文件
  restore-verify    在隔离目录验证备份
  self-check        检查发行包关键资源
  version           显示版本

配置文件：/etc/bookmark/bookmark.env
可用 QIANQI_ENV_FILE 指定其他路径；进程环境变量优先于配置文件。
"""

MODULE_COMMANDS = {
    "create-admin": "scripts.create_admin",
    "change-password": "scripts.change_password",
    "unlock-admin": "scripts.unlock_admin",
    "backup": "scripts.backup",
    "cleanup": "scripts.cleanup_import_jobs",
    "restore-verify": "scripts.restore_verify",
}


def _load_runtime_env() -> None:
    configured_path = os.environ.get("QIANQI_ENV_FILE")
    env_file = Path(configured_path) if configured_path else DEFAULT_ENV_FILE
    if configured_path and not env_file.is_file():
        raise SystemExit(f"配置文件不存在：{env_file}")
    if not env_file.is_file():
        return
    for key, value in _load_dotenv(env_file).items():
        os.environ.setdefault(key, value)


def _serve(args: list[str]) -> int:
    if args:
        raise SystemExit("serve 不接受参数；监听地址固定为 127.0.0.1:8000。")

    import uvicorn

    from app.config import load_settings
    from app.logging_setup import setup_logging
    from app.main import create_app

    settings = load_settings()
    setup_logging(settings.log_level, fmt=settings.log_format)
    uvicorn.run(
        create_app,
        factory=True,
        host="127.0.0.1",
        port=8000,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        log_level=settings.log_level.lower(),
    )
    return 0


def _migrate(args: list[str]) -> int:
    if args:
        raise SystemExit("migrate 不接受参数；目标固定为当前版本 head。")

    from alembic import command
    from alembic.config import Config

    config = Config(str(APP_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(APP_ROOT / "migrations"))
    command.upgrade(config, "head")
    print("MIGRATION_OK head")
    return 0


def _self_check(args: list[str]) -> int:
    if args:
        raise SystemExit("self-check 不接受参数。")
    required = (
        APP_ROOT / "alembic.ini",
        APP_ROOT / "migrations" / "env.py",
        APP_ROOT / "app" / "templates",
        APP_ROOT / "app" / "static",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("PACKAGE_INVALID missing=" + ",".join(missing), file=sys.stderr)
        return 1
    print(f"PACKAGE_OK version={VERSION}")
    return 0


def _run_module(command_name: str, args: list[str]) -> int:
    module = importlib.import_module(MODULE_COMMANDS[command_name])
    entry: Callable[[], int | None] = module.main
    previous_argv = sys.argv
    try:
        sys.argv = [f"qianqi {command_name}", *args]
        result = entry()
    finally:
        sys.argv = previous_argv
    return 0 if result is None else int(result)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(HELP.format(version=VERSION))
        return 0
    if args[0] in {"-V", "--version", "version"}:
        print(VERSION)
        return 0

    command_name, command_args = args[0], args[1:]
    if command_name not in {*MODULE_COMMANDS, "serve", "migrate", "self-check"}:
        print(f"未知命令：{command_name}\n", file=sys.stderr)
        print(HELP.format(version=VERSION), file=sys.stderr)
        return 2

    _load_runtime_env()
    if command_name == "serve":
        return _serve(command_args)
    if command_name == "migrate":
        return _migrate(command_args)
    if command_name == "self-check":
        return _self_check(command_args)
    return _run_module(command_name, command_args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
