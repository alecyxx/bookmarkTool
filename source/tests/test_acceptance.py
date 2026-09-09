"""阶段 10 验收自动化测试（BM-V1-901/902/903/906 代码化部分）。

- 全量回归由整个 tests/ 目录承担（本文件不跳过任何必测项）；
- 数据库升级：全新库到 head、模型/迁移一致（alembic check）、核心索引/外键存在；
- 安全汇总：登录默认落点、密码不落日志、恶意 CSV 拒绝、超大上传 413、超行数拒绝、
  固定搜索目标与不存在的服务端搜索端点。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from app.services.import_parser import parse_csv

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


class TestUpgradeAcceptance:
    """BM-V1-902：全新库到 head；重复 upgrade 幂等且数据不变；模型/迁移一致。"""

    def _upgrade(self, database_url: str) -> None:
        from alembic import command
        from alembic.config import Config as AlembicConfig

        config = AlembicConfig(str(PROJECT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(config, "head")

    def test_fresh_db_to_head_and_idempotent(self, tmp_path):
        db_file = tmp_path / "fresh.db"
        url = f"sqlite:///{db_file}"
        self._upgrade(url)
        conn = sqlite3.connect(db_file)
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for expected in (
            "users",
            "app_meta",
            "categories",
            "tags",
            "bookmarks",
            "bookmark_tags",
            "import_jobs",
            "alembic_version",
        ):
            assert expected in tables
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0002_bookmark_sort_weight"
        )
        conn.close()
        # 重复升级幂等
        self._upgrade(url)
        conn = sqlite3.connect(db_file)
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0002_bookmark_sort_weight"
        )
        conn.close()

    def test_model_metadata_matches_db(self, tmp_path):
        """alembic check：数据库结构与模型元数据无差异。"""
        from alembic import command
        from alembic.config import Config as AlembicConfig
        from alembic.util import CommandError

        db_file = tmp_path / "check.db"
        url = f"sqlite:///{db_file}"
        self._upgrade(url)
        config = AlembicConfig(str(PROJECT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url)
        try:
            command.check(config)
            diff_found = False
        except CommandError as exc:
            # check 以 CommandError 形式报告差异
            diff_found = "No new upgrade operations detected" not in str(exc)
        assert not diff_found

    def test_core_indexes_and_foreign_keys_present(self, tmp_path):
        db_file = tmp_path / "fk.db"
        self._upgrade(f"sqlite:///{db_file}")
        conn = sqlite3.connect(db_file)
        index_names = {row[1] for row in conn.execute("PRAGMA index_list('bookmarks')")}
        assert any("ix_bookmarks" in name for name in index_names)
        fk_categories = {row[2] for row in conn.execute("PRAGMA foreign_key_list('bookmarks')")}
        assert "categories" in fk_categories
        fk_users = {row[2] for row in conn.execute("PRAGMA foreign_key_list('users')")}
        assert fk_users == set()  # users 无外键属预期（根表）
        conn.close()

    def test_upgrade_keeps_data_counts(self, auth_client, db_session):
        """已升级库再次 upgrade：核心数据计数与抽样关系不变。"""
        from app.models.bookmark import Bookmark
        from app.models.category import Category

        category = Category(name="工作", normalized_name="工作")
        db_session.add(category)
        db_session.commit()
        bookmark = Bookmark(
            title="t",
            url="https://example.com/",
            normalized_url="https://example.com/",
            category_id=category.id,
        )
        db_session.add(bookmark)
        db_session.commit()
        self._upgrade(auth_client.app.state.settings.database_url)
        db_session.expire_all()
        assert db_session.query(Bookmark).count() == 1
        assert db_session.query(Category).count() == 1
        assert db_session.get(Bookmark, bookmark.id).category_id == category.id


class TestSecurityAcceptance:
    """BM-V1-903 代码化部分（其余安全测试分布于 test_auth_flow 等）。"""

    def test_login_default_landing(self, client, make_admin):
        make_admin(password=PASSWORD)
        client.get("/login")
        csrf = client.cookies.get("bookmark_csrf", "")
        response = client.post(
            "/auth/login",
            data={"username": "admin", "password": PASSWORD, "csrf_token": csrf},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    def test_password_never_in_logs(self, client, make_admin, caplog):
        make_admin(password=PASSWORD)
        client.get("/login")
        csrf = client.cookies.get("bookmark_csrf", "")
        with caplog.at_level(logging.INFO):
            client.post(
                "/auth/login",
                data={"username": "admin", "password": "WRONG-password-123", "csrf_token": csrf},
            )
            client.post(
                "/auth/login",
                data={"username": "admin", "password": PASSWORD, "csrf_token": csrf},
            )
        combined = "\n".join(record.getMessage() for record in caplog.records)
        assert "WRONG-password-123" not in combined
        assert PASSWORD not in combined

    def test_malicious_csv_rejected(self):
        payload = b'title,url\nx,=HYPERLINK("http://evil")\ny,javascript:alert(1)\n'
        result = parse_csv(payload)
        assert result.errors  # 公式与 javascript URL 均不执行且被拒绝/跳过

    def test_upload_over_size_limit_413(self, client, make_admin):
        make_admin(password=PASSWORD)
        client.get("/login")
        csrf = client.cookies.get("bookmark_csrf", "")
        client.post(
            "/auth/login",
            data={"username": "admin", "password": PASSWORD, "csrf_token": csrf},
        )
        big = b"x" * (11 * 1024 * 1024)
        response = client.post(
            "/api/imports/preview",
            data={"folder_policy": "category", "csrf_token": csrf},
            files={"file": ("big.html", big, "text/html")},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 413

    def test_import_rows_above_limit_rejected(self, auth_client):
        rows = ["title,url"] + [f"t{i},https://example.com/{i}" for i in range(60_000)]
        content = ("\n".join(rows)).encode("utf-8")
        response = auth_client.post(
            "/api/imports/preview",
            data={
                "folder_policy": "category",
                "csrf_token": auth_client.cookies.get("bookmark_csrf", ""),
            },
            files={"file": ("big.csv", content, "text/csv")},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 422
        import json as _json

        assert "上限" in _json.dumps(response.json(), ensure_ascii=False)

    def test_no_backend_search_and_fixed_targets(self):
        js = (PROJECT / "app" / "static" / "js" / "web-search.js").read_text(encoding="utf-8")
        assert js.count("https://") == 3  # 仅三个固定目标
        # 服务端无任何 /search 端点（路由表复查见 test_search_home::TestPrivacyBoundary）

    def test_upload_multipart_csrf_enforced(self, client, make_admin):
        make_admin(password=PASSWORD)
        client.get("/login")
        csrf = client.cookies.get("bookmark_csrf", "")
        client.post(
            "/auth/login",
            data={"username": "admin", "password": PASSWORD, "csrf_token": csrf},
        )
        response = client.post(
            "/api/imports/preview",
            data={"folder_policy": "category", "csrf_token": "forged-token"},
            files={"file": ("a.html", b"<DL><p></DL>", "text/html")},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 403


class TestNoSkippedMandatoryItems:
    """901：测试套件中不允许跳过必测项（无 skip/xfail 泄漏）。"""

    def test_no_skips_or_xfails_in_suite(self):
        skipped = []
        for test_file in (PROJECT / "tests").glob("test_*.py"):
            content = test_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("@pytest.mark.skip") or stripped.startswith(
                    "@pytest.mark.xfail"
                ):
                    skipped.append((test_file.name, stripped))
        assert skipped == []
