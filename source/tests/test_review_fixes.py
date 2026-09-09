"""CodeReview 修复回归测试（H1/H2/M3/M4/L1/M2/I1）。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from app.database import now_utc
from app.models.import_job import ImportJob
from app.services.import_service import (
    STATUS_SUCCEEDED,
    cleanup_orphan_files,
    expire_jobs,
    resolve_temp_root,
)

PASSWORD = "strong-password-1234"
PROJECT = Path(__file__).resolve().parents[1]

CSV_SINGLE = "title,url\n覆盖目标,https://example.com/cover\n"


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


def _headers(auth_client) -> dict:
    return {"X-CSRF-Token": auth_client.cookies.get("bookmark_csrf", "")}


class TestH1ReorderRevision:
    """分类树 fragment 携带 tree revision；前端脚本提交 reorder 时带上（评审 H1）。"""

    def test_tree_fragment_carries_revision(self, auth_client, db_session):
        from app.models.category import Category

        db_session.add(Category(name="A", normalized_name="a"))
        db_session.commit()
        html = auth_client.get("/api/categories/tree", headers={"Accept": "text/html"}).text
        assert 'data-revision="' in html

    def test_reorder_payload_has_revision_in_script(self):
        script = (PROJECT / "app" / "static" / "js" / "categories-ui.js").read_text(
            encoding="utf-8"
        )
        assert "category_tree_revision" in script
        assert 'getAttribute("data-revision")' in script


class TestH2AdminList:
    def test_admin_list_does_not_crash(self, db_session, make_admin, monkeypatch, capsys):
        make_admin(password=PASSWORD, username="boss")
        from scripts import admin as admin_tool

        monkeypatch.setattr(admin_tool, "_session", lambda: db_session)
        admin_tool.cmd_list()  # 之前引用不存在列必然 AttributeError
        out = capsys.readouterr().out
        assert "boss" in out
        assert "locked_until" in out


class TestM3OverwriteBumpsVersion:
    def test_overwrite_increments_bookmark_version(self, auth_client, db_session):
        from app.models.bookmark import Bookmark

        db_session.add(
            Bookmark(
                title="旧标题",
                url="https://example.com/cover",
                normalized_url="https://example.com/cover",
            )
        )
        db_session.commit()
        version_before = db_session.query(Bookmark).first().version
        response = auth_client.post(
            "/api/imports/preview",
            data={
                "folder_policy": "ignore",
                "csrf_token": auth_client.cookies.get("bookmark_csrf", ""),
            },
            files={"file": ("cover.csv", CSV_SINGLE.encode("utf-8"), "text/csv")},
            headers={"Accept": "text/html"},
        )
        assert response.status_code == 200
        job_id = response.text.split('data-job-id="')[1].split('"')[0]
        auth_client.put(
            f"/api/imports/{job_id}/options",
            json={"duplicate_policy": "overwrite", "folder_policy": "ignore"},
            headers=_headers(auth_client),
        )
        execute = auth_client.post(f"/api/imports/{job_id}/execute", headers=_headers(auth_client))
        assert execute.status_code == 200, execute.text
        bookmark = db_session.query(Bookmark).first()
        assert bookmark.title == "覆盖目标"
        assert bookmark.version == version_before + 1


class TestM4PreviewStrongCsrf:
    def test_preview_rejects_invalid_session_signature(self, client, make_admin):
        """token 与 cookie 一致但会话载荷无效（版本/签名不符）时仍拒绝（评审 M4）。"""
        make_admin(password=PASSWORD)
        client.get("/login")
        csrf = client.cookies.get("bookmark_csrf", "")
        client.post(
            "/auth/login",
            data={"username": "admin", "password": PASSWORD, "csrf_token": csrf},
        )
        settings = client.app.state.settings
        # 破坏会话 cookie（改为伪造值），保留 csrf cookie 原值
        client.cookies.set(settings.session_cookie_name, "forged-session-value")
        response = client.post(
            "/api/imports/preview",
            data={"folder_policy": "category", "csrf_token": csrf},
            files={"file": ("a.csv", b"title,url\nx,https://a.example\n", "text/csv")},
            headers={"Accept": "application/json"},
        )
        assert response.status_code in (401, 403)  # 会话被篡改后：守卫 401 或 CSRF 403


class TestL1TrashUrlEncoding:
    def test_trash_pagination_encodes_query(self, auth_client, db_session):
        from app.database import now_utc as _now
        from app.models.bookmark import Bookmark

        for index in range(60):
            db_session.add(
                Bookmark(
                    title=f"a&b 目标 {index}",
                    url=f"https://example.com/{index}",
                    normalized_url=f"https://example.com/{index}",
                    deleted_at=_now(),
                )
            )
        db_session.commit()
        html = auth_client.get("/trash?q=a%26b&page=2", headers={"Accept": "text/html"}).text
        # 搜索词中的 & 在分页链接中被编码（而非拆成新参数）
        assert "a%26b" in html  # 搜索词中的 & 被编码为 %26（翻页链接）
        assert 'value="a&amp;b"' in html  # 输入框回显经 HTML 转义


class TestM2TempFileLifecycle:
    def _make_terminal_job(self, db_session, settings, tmp_path) -> ImportJob:
        from app.services.import_service import sha256_bytes

        job = ImportJob(
            id="terminal-job-0001",
            source_type="CSV",
            original_filename="old.csv",
            file_sha256=sha256_bytes(b"x"),
            temp_file_key="terminal-job-0001",
            session_nonce_hash="n",
            options_json="{}",
            summary_json="{}",
            category_tree_revision=1,
            status=STATUS_SUCCEEDED,
            expires_at=now_utc() + timedelta(hours=1),
            executed_at=now_utc() - timedelta(hours=48),  # 超过保留期
        )
        db_session.add(job)
        db_session.commit()
        root = resolve_temp_root(settings)
        root.mkdir(parents=True, exist_ok=True)
        (root / "terminal-job-0001.src").write_bytes(b"x")
        return job

    def test_expire_removes_terminal_jobs_and_files(self, auth_client, db_session, tmp_path):
        settings = auth_client.app.state.settings
        job = self._make_terminal_job(db_session, settings, tmp_path)
        cleanup = expire_jobs(db_session, settings)
        db_session.commit()
        assert cleanup["terminal_removed"] == 1
        assert db_session.get(ImportJob, job.id) is None
        assert not resolve_temp_root(settings).joinpath(f"{job.temp_file_key}.src").exists()

    def test_orphan_files_cleaned(self, auth_client, db_session, tmp_path):
        settings = auth_client.app.state.settings
        root = resolve_temp_root(settings)
        root.mkdir(parents=True, exist_ok=True)
        orphan = root / "no-record-orphan.src"
        orphan.write_bytes(b"x")
        old_time = (now_utc() - timedelta(hours=2)).timestamp()
        import os

        os.utime(orphan, (old_time, old_time))
        removed = cleanup_orphan_files(db_session, settings, older_seconds=3600)
        db_session.commit()
        assert removed == 1
        assert not orphan.exists()
        # 有记录的文件不受影响
        job = self._make_terminal_job(db_session, settings, tmp_path)
        tracked_file = root / f"{job.temp_file_key}.src"
        cleanup_orphan_files(db_session, settings)
        db_session.commit()
        assert tracked_file.exists()


class TestI1FaviconValidation:
    def test_favicon_scheme_whitelist(self, auth_client):
        headers = _headers(auth_client)
        for bad in (
            "javascript:alert(1)",
            "data:image/png;base64,AAA",
            "file:///etc/passwd",
            "https://example.com/x.svg",
        ):
            response = auth_client.post(
                "/api/bookmarks",
                json={
                    "title": "t",
                    "url": "https://ok.example/",
                    "favicon_url": bad,
                },
                headers=headers,
            )
            assert response.status_code == 422, bad
        good = auth_client.post(
            "/api/bookmarks",
            json={
                "title": "t2",
                "url": "https://ok2.example/",
                "favicon_url": "https://example.com/favicon.png",
            },
            headers=headers,
        )
        assert good.status_code in (200, 201)
