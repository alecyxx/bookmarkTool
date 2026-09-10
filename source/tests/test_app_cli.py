"""单文件发行入口测试。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from app import cli


def test_help_and_version_do_not_require_config(capsys):
    assert cli.main(["--help"]) == 0
    assert "qianqi <命令>" in capsys.readouterr().out

    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == cli.VERSION


def test_unknown_command_returns_two(capsys):
    assert cli.main(["unknown"]) == 2
    assert "未知命令" in capsys.readouterr().err


def test_env_file_loads_without_overriding_process_env(tmp_path, monkeypatch):
    env_file = tmp_path / "bookmark.env"
    env_file.write_text("APP_ENV=production\nLOG_LEVEL=warning\n", encoding="utf-8")
    monkeypatch.setenv("QIANQI_ENV_FILE", str(env_file))
    monkeypatch.setenv("APP_ENV", "lan")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    cli._load_runtime_env()

    assert cli.os.environ["APP_ENV"] == "lan"
    assert cli.os.environ["LOG_LEVEL"] == "warning"


def test_module_command_forwards_arguments_and_restores_argv(monkeypatch):
    captured: list[str] = []

    def fake_main() -> int:
        captured.extend(sys.argv)
        return 7

    monkeypatch.setattr(cli, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(
        cli.importlib,
        "import_module",
        lambda _name: SimpleNamespace(main=fake_main),
    )
    original_argv = sys.argv

    assert cli.main(["backup", "--type", "weekly"]) == 7
    assert captured == ["qianqi backup", "--type", "weekly"]
    assert sys.argv is original_argv


def test_self_check_finds_source_resources(capsys):
    assert cli.main(["self-check"]) == 0
    assert "PACKAGE_OK" in capsys.readouterr().out
