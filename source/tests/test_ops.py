"""运维能力测试（BM-V1-801~808 自动化部分）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from app.database import now_utc
from app.models.bookmark import Bookmark
from app.services import ops
from app.services.ops import (
    apply_retention,
    backup_name,
    check_ready,
    create_backup,
    database_file_path,
    restore_verify,
    retention_plan,
    sha256_file,
    verify_backup_file,
)

PASSWORD = "strong-password-1234"
PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def auth_client(client, make_admin):
    make_admin(password=PASSWORD)
    client.get("/login")
    csrf = client.cookies.get("bookmark_csrf", "")
    client.post(
        "/auth/login",
        data={"username": "admin", "password": PASSWORD, "csrf_token": csrf},
    )
    return client


def _seed_bookmarks(db_session):
    from app.models.category import Category
    from app.models.tag import Tag

    parent = Category(name="工作", normalized_name="工作")
    db_session.add(parent)
    db_session.flush()
    child = Category(name="开发", normalized_name="开发", parent_id=parent.id)
    tag = Tag(name="标签", normalized_name="标签")
    db_session.add_all([child, tag])
    db_session.flush()
    bookmark = Bookmark(
        title="t",
        url="https://example.com/",
        normalized_url="https://example.com/",
        category_id=child.id,
    )
    db_session.add(bookmark)
    db_session.flush()
    bookmark.tags.append(tag)
    db_session.add(
        Bookmark(
            title="trashed",
            url="https://example.com/t",
            normalized_url="https://example.com/t",
            deleted_at=now_utc(),
        )
    )
    db_session.commit()


class TestReadyProbe:
    def test_ready_ok(self, auth_client):
        response = auth_client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_missing_db(self):
        result = check_ready("sqlite:////nonexistent-dir/bookmarks.db")
        assert result == {"status": "not_ready", "reason": "db_missing"}

    def test_ready_relative_url_probes_resolved_database(self, tmp_path, monkeypatch):
        from app import config

        app_root = tmp_path / "app-root"
        working_dir = tmp_path / "working-dir"
        app_root.mkdir()
        working_dir.mkdir()
        monkeypatch.setattr(config, "APP_ROOT", app_root)
        monkeypatch.chdir(working_dir)

        db_file = app_root / "ready.db"
        conn = sqlite3.connect(db_file)
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute(
            "INSERT INTO alembic_version(version_num) VALUES (?)",
            (ops.EXPECTED_DB_REVISION,),
        )
        conn.commit()
        conn.close()

        assert check_ready("sqlite:///ready.db") == {"status": "ok", "reason": None}
        assert not (working_dir / "ready.db").exists()
        conn = sqlite3.connect(db_file)
        try:
            assert conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_ready_probe'"
            ).fetchone()
        finally:
            conn.close()

    def test_ready_revision_mismatch(self, client, make_admin, db_session):
        make_admin(password=PASSWORD)
        db_file = database_file_path(client.app.state.settings.database_url)
        conn = sqlite3.connect(db_file)
        conn.execute("UPDATE alembic_version SET version_num='wrong_head'")
        conn.commit()
        conn.close()
        response = client.get("/health/ready")
        assert response.status_code == 503
        # 不泄露详情
        assert response.json() == {"status": "not_ready"}


class TestBackup:
    def test_backup_roundtrip(self, client, make_admin, db_session, tmp_path):
        make_admin(password=PASSWORD)
        _seed_bookmarks(db_session)
        backup_dir = tmp_path / "backups"
        backup = create_backup(client.app.state.settings.database_url, backup_dir, "daily")
        assert backup.exists()
        assert backup.name.startswith("daily-")
        assert ops.integrity_ok(backup)
        manifest = backup.with_suffix(backup.suffix + ".sha256")
        assert manifest.exists()
        assert sha256_file(backup) == manifest.read_text().strip().split(" ")[0]
        # 源库不受影响
        assert db_session.query(Bookmark).count() == 2

    def test_backup_failure_keeps_no_partial(self, tmp_path):
        backup_dir = tmp_path / "backups"
        with pytest.raises((RuntimeError, sqlite3.Error)):
            create_backup("sqlite:////nonexistent-dir/bookmarks.db", backup_dir, "daily")
        assert not backup_dir.exists() or list(backup_dir.iterdir()) == []

    def test_backup_rejects_empty_sqlite_file(self, tmp_path):
        source = tmp_path / "empty.db"
        sqlite3.connect(source).close()
        with pytest.raises(RuntimeError, match="schema is not initialized"):
            create_backup(f"sqlite:///{source}", tmp_path / "backups", "daily")

    def test_manifest_publish_failure_keeps_no_partial(
        self, client, make_admin, tmp_path, monkeypatch
    ):
        make_admin(password=PASSWORD)
        backup_dir = tmp_path / "backups"
        real_replace = ops.os.replace
        replace_calls = 0

        def fail_second_replace(source, target):
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 2:
                raise OSError("simulated manifest publish failure")
            return real_replace(source, target)

        monkeypatch.setattr(ops.os, "replace", fail_second_replace)
        with pytest.raises(OSError, match="manifest publish failure"):
            create_backup(client.app.state.settings.database_url, backup_dir, "daily")
        assert not list(backup_dir.glob("*.db"))
        assert not list(backup_dir.glob("*.sha256"))

    def _write_backup(self, directory: Path, backup_type: str, stamp: datetime) -> Path:
        path = directory / backup_name(backup_type, stamp)
        path.write_bytes(b"x")
        return path

    def test_retention_daily_keeps_latest_seven(self, tmp_path):
        now = datetime(2026, 9, 9, 12, 0, 0)
        files = [
            self._write_backup(tmp_path, "daily", datetime(2026, 9, day, 3, 0, 0))
            for day in range(1, 10)  # 9 份（9/1 ~ 9/9）
        ]
        keep = retention_plan(files, now, daily=7, weekly=4, monthly=12)
        assert len(keep) == 7
        assert any("20260903" in p.name for p in keep)  # 最新 7 份保留（9/3 ~ 9/9）
        assert not any("20260901" in p.name or "20260902" in p.name for p in keep)

    def test_retention_weekly_keeps_four_weeks(self, tmp_path):
        now = datetime(2026, 9, 9, 12, 0, 0)  # 周三
        files = []
        stamp = datetime(2026, 9, 7, 3, 0, 0)  # 最近周一
        for _ in range(6):
            files.append(self._write_backup(tmp_path, "weekly", stamp))
            stamp = stamp - timedelta(days=7)
        keep = retention_plan(files, now, daily=7, weekly=4, monthly=12)
        assert len(keep) == 4

    def test_retention_weekly_handles_early_weeks_and_iso_week_53(self, tmp_path):
        now = datetime(2021, 1, 4, 12, 0, 0)  # 2021-W01；上一周是 2020-W53
        current = self._write_backup(tmp_path, "weekly", datetime(2021, 1, 4, 3, 0, 0))
        week_53 = self._write_backup(tmp_path, "weekly", datetime(2020, 12, 28, 3, 0, 0))
        week_52 = self._write_backup(tmp_path, "weekly", datetime(2020, 12, 21, 3, 0, 0))
        keep = retention_plan(
            [current, week_53, week_52],
            now,
            daily=7,
            weekly=3,
            monthly=12,
        )
        assert keep == {current, week_53, week_52}

    def test_retention_monthly_keeps_twelve_months(self, tmp_path):
        now = datetime(2026, 9, 9, 12, 0, 0)
        files = []
        stamp = datetime(2026, 9, 5, 3, 0, 0)
        for _ in range(15):
            files.append(self._write_backup(tmp_path, "monthly", stamp))
            year, month = (stamp.year, stamp.month - 1) if stamp.month > 1 else (stamp.year - 1, 12)
            stamp = stamp.replace(year=year, month=month, day=5)
        keep = retention_plan(files, now, daily=7, weekly=4, monthly=12)
        assert len(keep) == 12

    def test_apply_retention_removes_expired(self, tmp_path):
        now = datetime(2026, 9, 9, 12, 0, 0)
        directory = tmp_path / "backups"
        directory.mkdir()
        files = [
            self._write_backup(directory, "daily", datetime(2026, 8, day, 3, 0, 0))
            for day in range(29, 32)
        ]
        files += [
            self._write_backup(directory, "daily", datetime(2026, 9, day, 3, 0, 0))
            for day in range(1, 10)
        ]
        # 12 份中删除最早 5 份（8/29 ~ 9/2），保留最近 7 份
        removed = apply_retention(directory, now=now, daily=7, weekly=4, monthly=12)
        assert len(removed) == 5
        assert all(
            p.name.startswith("daily-202608") or p.name.startswith("daily-2026090") for p in removed
        )
        kept = [p for p in directory.iterdir() if p.suffix == ".db"]
        assert len(kept) == 7
        assert any("20260909" in p.name for p in kept)
        # 被删文件的 sha256 清单一并清理
        assert all(not p.with_suffix(p.suffix + ".sha256").exists() for p in removed)


class TestRestoreVerify:
    def test_restore_in_isolated_dir(self, client, make_admin, db_session, tmp_path):
        make_admin(password=PASSWORD)
        _seed_bookmarks(db_session)
        backup_dir = tmp_path / "backups"
        backup = create_backup(client.app.state.settings.database_url, backup_dir, "daily")
        work = tmp_path / "restore-work"
        stats = restore_verify(client.app.state.settings.database_url, backup, work)
        assert stats["bookmarks"] == 2
        assert stats["trashed"] == 1
        assert stats["categories"] == 2
        assert stats["tags"] == 1
        assert stats["users"] == 1
        assert stats["sample_category_linked"] is True
        assert stats["revision"] == ops.EXPECTED_DB_REVISION
        # 生产库未被覆盖
        source = database_file_path(client.app.state.settings.database_url)
        conn = sqlite3.connect(source)
        assert conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0] == 2
        conn.close()

    def test_verify_rejects_corrupt_backup(self, tmp_path):
        backup = tmp_path / backup_name("daily", datetime(2026, 9, 9, 3, 0, 0))
        backup.write_bytes(b"not-a-db")
        backup.with_suffix(backup.suffix + ".sha256").write_text(
            f"{sha256_file(backup)}  {backup.name}\n",
            encoding="ascii",
        )
        with pytest.raises(RuntimeError, match="integrity_check failed"):
            verify_backup_file(backup)

    def test_verify_rejects_backup_without_manifest(self, tmp_path):
        backup = tmp_path / backup_name("daily", datetime(2026, 9, 9, 3, 0, 0))
        conn = sqlite3.connect(backup)
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="manifest missing"):
            verify_backup_file(backup)


class TestAdminTool:
    def test_reset_password_unlocks_and_rotates_session(self, db_session, make_admin, monkeypatch):
        make_admin(password=PASSWORD, username="boss")
        from scripts import admin as admin_tool

        # 指向测试库并复用其会话
        monkeypatch.setattr(admin_tool, "_session", lambda: db_session)

        from app.models.user import User

        user = db_session.query(User).filter(User.username == "boss").first()
        user.failed_login_count = 5
        user.locked_until = now_utc()
        db_session.commit()
        admin_tool.cmd_reset_password("boss", "brand-new-pass-123")
        db_session.expire_all()
        user = db_session.query(User).filter(User.username == "boss").first()
        assert user.failed_login_count == 0
        assert user.locked_until is None
        assert user.session_version > 1
        from app.services.security import verify_password

        assert not verify_password(PASSWORD, user.password_hash)
        assert verify_password("brand-new-pass-123", user.password_hash)

    def test_unlock(self, db_session, make_admin, monkeypatch):
        make_admin(password=PASSWORD, username="boss")
        from scripts import admin as admin_tool

        monkeypatch.setattr(admin_tool, "_session", lambda: db_session)
        from app.models.user import User

        user = db_session.query(User).filter(User.username == "boss").first()
        user.failed_login_count = 8
        db_session.commit()
        admin_tool.cmd_unlock("boss")
        db_session.expire_all()
        user = db_session.query(User).filter(User.username == "boss").first()
        assert user.failed_login_count == 0


class TestDeployArtifacts:
    """801/802/806 交付物静态检查（镜像构建/漏洞扫描需真实环境，见交付记录已知限制）。"""

    def test_dockerfile(self):
        dockerfile = (PROJECT / "Dockerfile").read_text(encoding="utf-8")
        assert "python:3.14.3-slim-bookworm" in dockerfile
        assert "USER app" in dockerfile
        assert "useradd --system --uid 10001" in dockerfile
        assert "pip install" in dockerfile

    def test_dockerignore(self):
        content = (PROJECT / ".dockerignore").read_text(encoding="utf-8")
        for line in (
            ".venv/",
            "dev_data/",
            "tests/",
            "__pycache__/",
            ".coverage",
            "coverage.xml",
            "_dbg*.py",
            "_fix*.py",
        ):
            assert line in content
        assert "scripts/" not in content  # 运维脚本需进镜像（容器内备份/恢复）

    def test_compose_structure(self):
        compose = (PROJECT.parent / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        assert "  app:" in compose and "  caddy:" in compose
        assert "read_only: true" in compose
        assert '"8000"' in compose
        assert "80:80" in compose and "443:443" in compose
        assert "healthcheck" in compose
        assert "Caddyfile:/etc/caddy/Caddyfile:ro" in compose
        assert "app-data:/data:rw" in compose
        assert "/health/ready" in compose  # 健康检查命中 ready 探针

    def test_caddyfile(self):
        caddy = (PROJECT.parent / "deploy" / "Caddyfile").read_text(encoding="utf-8")
        assert "reverse_proxy app:8000" in caddy
        assert "{$DOMAIN" in caddy

    def test_env_template_has_required_secrets(self):
        env = (PROJECT.parent / "deploy" / ".env.production.example").read_text(encoding="utf-8")
        assert "SESSION_SECRET" in env
        assert "openssl rand -hex 32" in env
        assert "DOMAIN" in env
        # 会话与 CSRF 共用 SESSION_SECRET 派生；模板不得虚构独立 CSRF_SECRET
        assert "CSRF_SECRET" not in env

    def test_cron_example_covers_all_types(self):
        cron = (PROJECT.parent / "deploy" / "cron-backup.example").read_text(encoding="utf-8")
        for backup_type in ("daily", "weekly", "monthly"):
            assert f"scripts.backup --type {backup_type}" in cron
        assert "restore_verify" in cron

    def test_backup_and_restore_cli_present(self):
        assert (PROJECT / "scripts" / "backup.py").exists()
        assert (PROJECT / "scripts" / "restore_verify.py").exists()
        assert (PROJECT / "scripts" / "admin.py").exists()
