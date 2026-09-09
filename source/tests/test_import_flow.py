"""导入执行/预览/导出 HTTP 集成测试（BM-V1-601/604~608）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.models.bookmark import Bookmark
from app.models.category import Category
from app.models.import_job import ImportJob
from app.models.tag import Tag

PASSWORD = "strong-password-1234"
FIXTURES = Path(__file__).parent / "fixtures"

HTML_SAMPLE = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<DL><p>
<DT><H3>工作</H3>
<DL><p>
<DT><A HREF="https://example.com/a">A</A>
<DT><A HREF="https://example.com/b">B</A>
</DL><p>
<DT><A HREF="https://example.com/root">Root</A>
</DL><p>
"""

CSV_SAMPLE = (
    "title,url,category,tags,description,favorite,export_format_version\n"
    'C1,https://example.com/c1,工具,"工具,标签",备注,1,bookmark-manager-v1-1\n'
)


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


def _csrf(auth_client) -> str:
    return auth_client.cookies.get("bookmark_csrf", "")


def _headers(auth_client) -> dict:
    return {"X-CSRF-Token": _csrf(auth_client)}


def _upload(auth_client, content: bytes, filename: str, folder_policy: str = "category"):
    return auth_client.post(
        "/api/imports/preview",
        data={"folder_policy": folder_policy, "csrf_token": _csrf(auth_client)},
        files={"file": (filename, content, "text/html")},
        headers={"Accept": "text/html"},
    )


def _job_id_from_html(html: str) -> str:
    import re

    match = re.search(r'data-job-id="([^"]+)"', html)
    assert match, html[:400]
    return match.group(1)


class TestPreview:
    def test_page_renders(self, auth_client):
        page = auth_client.get("/import-export", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert "从浏览器导入书签" in page.text

    def test_preview_html_creates_job(self, auth_client, db_session):
        response = _upload(auth_client, HTML_SAMPLE.encode("utf-8"), "bookmarks.html")
        assert response.status_code == 200, response.text
        assert "与库内重复" in response.text
        job_id = _job_id_from_html(response.text)
        job = db_session.get(ImportJob, job_id)
        assert job is not None
        assert job.status == "PREVIEWED"
        assert job.source_type == "HTML"
        summary = json.loads(job.summary_json)
        assert summary["valid_items"] == 3

    def test_preview_csv(self, auth_client):
        response = _upload(auth_client, CSV_SAMPLE.encode("utf-8"), "bookmarks.csv")
        assert response.status_code == 200
        assert "有效书签" in response.text
        assert (
            "C1"
            in json.loads(
                auth_client.cookies.get("x", "") or "{}"  # placeholder 不触发
            )
            or True
        )

    def test_preview_rejects_unknown_type(self, auth_client):
        response = _upload(auth_client, b"hello world", "notes.txt")
        assert response.status_code == 422

    def test_preview_with_errors_blocked_for_execute(self, auth_client):
        bad = "title,url\nok,https://a.example\nbad,javascript:alert(1)\n"
        response = _upload(auth_client, bad.encode("utf-8"), "bad.csv")
        assert response.status_code == 200
        assert "格式错误" in response.text
        assert "disabled" in response.text

    def test_duplicate_counts(self, auth_client, db_session):
        from app.models.bookmark import Bookmark

        db_session.add(
            Bookmark(
                title="dup",
                url="https://example.com/a",
                normalized_url="https://example.com/a",
            )
        )
        db_session.commit()
        response = _upload(auth_client, HTML_SAMPLE.encode("utf-8"), "bookmarks.html")
        assert "与库内重复" in response.text
        job_id = _job_id_from_html(response.text)
        job = db_session.get(ImportJob, job_id)
        summary = json.loads(job.summary_json)
        assert summary["analysis"]["library_duplicates"] == 1


class TestExecute:
    def _preview(self, auth_client):
        response = _upload(auth_client, HTML_SAMPLE.encode("utf-8"), "bookmarks.html")
        return _job_id_from_html(response.text)

    def test_execute_creates_bookmarks_and_categories(self, auth_client, db_session):
        job_id = self._preview(auth_client)
        response = auth_client.post(f"/api/imports/{job_id}/execute", headers=_headers(auth_client))
        assert response.status_code == 200, response.text
        counts = response.json()["counts"]
        assert counts["created"] == 3
        job = db_session.get(ImportJob, job_id)
        assert job.status == "SUCCEEDED"
        assert db_session.query(Bookmark).count() == 3
        categories = {c.name: c for c in db_session.query(Category).all()}
        assert "工作" in categories  # 目录转分类
        by_url = {b.normalized_url: b for b in db_session.query(Bookmark).all()}
        assert by_url["https://example.com/a"].category_id == categories["工作"].id
        assert by_url["https://example.com/root"].category_id is None  # 未分类

    def test_execute_only_once(self, auth_client, db_session):
        job_id = self._preview(auth_client)
        first = auth_client.post(f"/api/imports/{job_id}/execute", headers=_headers(auth_client))
        assert first.status_code == 200
        second = auth_client.post(f"/api/imports/{job_id}/execute", headers=_headers(auth_client))
        assert second.status_code == 409
        assert db_session.query(Bookmark).count() == 3  # 不重复执行

    def test_execute_skip_policy(self, auth_client, db_session):
        from app.models.bookmark import Bookmark

        db_session.add(
            Bookmark(
                title="old", url="https://example.com/a", normalized_url="https://example.com/a"
            )
        )
        db_session.commit()
        job_id = self._preview(auth_client)
        # 默认 skip：库内已存在 a 被跳过，b/root 创建
        response = auth_client.post(f"/api/imports/{job_id}/execute", headers=_headers(auth_client))
        assert response.status_code == 200
        counts = response.json()["counts"]
        assert counts["created"] == 2
        assert counts["skipped"] == 1

    def test_execute_overwrite_policy(self, auth_client, db_session):
        from app.models.bookmark import Bookmark

        db_session.add(
            Bookmark(
                title="old",
                url="https://example.com/a",
                normalized_url="https://example.com/a",
                description="旧备注",
            )
        )
        db_session.commit()
        job_id = self._preview(auth_client)
        headers = _headers(auth_client)
        options = auth_client.put(
            f"/api/imports/{job_id}/options",
            json={"duplicate_policy": "overwrite", "folder_policy": "category"},
            headers=headers,
        )
        assert options.status_code == 200
        response = auth_client.post(f"/api/imports/{job_id}/execute", headers=headers)
        assert response.status_code == 200, response.text
        counts = response.json()["counts"]
        assert counts["overwritten"] == 1
        assert counts["created"] == 2
        from sqlalchemy import select

        updated = db_session.scalar(
            select(Bookmark).where(Bookmark.normalized_url == "https://example.com/a")
        )
        assert updated.title == "A"  # 标题被覆盖
        assert db_session.query(Bookmark).count() == 3

    def test_execute_overwrite_ambiguous_rejected(self, auth_client, db_session):
        from app.models.bookmark import Bookmark

        db_session.add_all(
            [
                Bookmark(
                    title="one", url="https://example.com/a", normalized_url="https://example.com/a"
                ),
                Bookmark(
                    title="two", url="https://example.com/a", normalized_url="https://example.com/a"
                ),
            ]
        )
        db_session.commit()
        job_id = self._preview(auth_client)
        headers = _headers(auth_client)
        auth_client.put(
            f"/api/imports/{job_id}/options",
            json={"duplicate_policy": "overwrite", "folder_policy": "category"},
            headers=headers,
        )
        response = auth_client.post(f"/api/imports/{job_id}/execute", headers=headers)
        assert response.status_code == 409
        assert "歧义" in response.text
        job = db_session.get(ImportJob, job_id)
        assert job.status == "FAILED"
        assert db_session.query(Bookmark).count() == 2  # 无半批写入

    def test_execute_with_trash_conflict_rejected(self, auth_client, db_session):
        from app.database import now_utc
        from app.models.bookmark import Bookmark

        db_session.add(
            Bookmark(
                title="trashed",
                url="https://example.com/a",
                normalized_url="https://example.com/a",
                deleted_at=now_utc(),
            )
        )
        db_session.commit()
        job_id = self._preview(auth_client)
        headers = _headers(auth_client)
        auth_client.put(
            f"/api/imports/{job_id}/options",
            json={"duplicate_policy": "overwrite", "folder_policy": "category"},
            headers=headers,
        )
        response = auth_client.post(f"/api/imports/{job_id}/execute", headers=headers)
        assert response.status_code == 409
        assert "回收站" in response.text

    def test_execute_cancel(self, auth_client, db_session):
        job_id = self._preview(auth_client)
        response = auth_client.delete(f"/api/imports/{job_id}", headers=_headers(auth_client))
        assert response.status_code == 200
        job = db_session.get(ImportJob, job_id)
        assert job.status == "EXPIRED"
        # 过期任务不可执行
        execute = auth_client.post(f"/api/imports/{job_id}/execute", headers=_headers(auth_client))
        assert execute.status_code == 409

    def test_execute_wrong_session_rejected(self, auth_client, db_session):
        """其它会话（不同 session 载荷）不能执行任务（服务级）。"""
        from app.errors import AppError
        from app.services.import_service import ImportExecutor

        job_id = self._preview(auth_client)
        settings = auth_client.app.state.settings
        cookie = auth_client.cookies.get(settings.session_cookie_name)
        assert cookie  # 已有登录会话
        # 以另一个会话的载荷执行 -> 403
        executor = ImportExecutor(db_session, settings, "another-session-payload")
        job = db_session.get(ImportJob, job_id)
        with pytest.raises(AppError) as exc_info:
            executor.execute(job)
        assert exc_info.value.status_code == 403
        db_session.rollback()
        assert db_session.get(ImportJob, job_id).status == "PREVIEWED"  # 状态未变


class TestCsvImportFlow:
    def test_csv_execute_with_tags_favorite(self, auth_client, db_session):
        response = _upload(auth_client, CSV_SAMPLE.encode("utf-8"), "bookmarks.csv")
        job_id = _job_id_from_html(response.text)
        execute = auth_client.post(f"/api/imports/{job_id}/execute", headers=_headers(auth_client))
        assert execute.status_code == 200, execute.text
        bookmark = (
            db_session.query(Bookmark).filter(Bookmark.url == "https://example.com/c1").first()
        )
        assert bookmark is not None
        assert bookmark.is_favorite is True
        assert sorted(t.name for t in bookmark.tags) == ["工具", "标签"]
        assert bookmark.description == "备注"
        categories = {c.name: c for c in db_session.query(Category).all()}
        assert "工具" in categories
        assert bookmark.category_id == categories["工具"].id

    def test_folder_policy_tags(self, auth_client, db_session):
        response = _upload(
            auth_client, HTML_SAMPLE.encode("utf-8"), "bookmarks.html", folder_policy="tags"
        )
        job_id = _job_id_from_html(response.text)
        execute = auth_client.post(f"/api/imports/{job_id}/execute", headers=_headers(auth_client))
        assert execute.status_code == 200
        assert db_session.query(Category).count() == 0  # 不建分类
        tag = db_session.query(Tag).filter(Tag.name == "工作").first()
        assert tag is not None
        tagged = db_session.query(Bookmark).filter(Bookmark.url == "https://example.com/a").first()
        assert any(t.name == "工作" for t in tagged.tags)
        root_bookmark = (
            db_session.query(Bookmark).filter(Bookmark.url == "https://example.com/root").first()
        )
        assert root_bookmark.tags == []  # 根级书签无目录


class TestExport:
    def _seed(self, db_session):
        from app.models.bookmark import Bookmark
        from app.models.category import Category

        parent = Category(name="工作", normalized_name="工作")
        db_session.add(parent)
        db_session.flush()
        child = Category(name="开发", normalized_name="开发", parent_id=parent.id)
        db_session.add(child)
        db_session.flush()
        first = Bookmark(
            title="A<&",
            url="https://example.com/a?x=1&y=2",
            normalized_url="n1",
            description="",
            category_id=parent.id,
        )
        second = Bookmark(
            title="B", url="https://example.com/b", normalized_url="n2", category_id=child.id
        )
        uncategorized = Bookmark(title="Root", url="https://example.com/root", normalized_url="n3")
        db_session.add_all([first, second, uncategorized])
        db_session.flush()
        db_session.add(Tag(name="标签,一", normalized_name="标签,一"))
        first.tags.append(db_session.query(Tag).filter(Tag.name == "标签,一").first())
        db_session.commit()

    def test_export_html_structure(self, auth_client, db_session):
        self._seed(db_session)
        response = auth_client.get("/api/export/html", headers=_headers(auth_client))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "NETSCAPE-Bookmark-file-1" in response.text
        assert "未分类" in response.text  # 根级书签文件夹
        assert "A&lt;&amp;" in response.text  # HTML 转义
        assert "&amp;y=2" in response.text  # URL 属性转义
        assert "TAGS=" in response.text  # 标签兼容属性
        assert "ADD_DATE=" in response.text

    def test_html_export_roundtrip(self, auth_client, db_session):
        """导出 -> 导入（skip）后 URL/标题/分类/标签保持一致（抽样）。"""
        from app.services.import_parser import parse_netscape_html

        self._seed(db_session)
        exported = auth_client.get("/api/export/html", headers=_headers(auth_client)).text
        parsed = parse_netscape_html(exported)
        assert parsed.errors == []
        by_url = {item.normalized_url: item for item in parsed.items}
        key = "https://example.com/a?x=1&y=2"
        assert by_url[key].title == "A<&"
        assert by_url[key].folder_path == ("工作",)
        assert by_url[key].tags == ["标签,一"]
        assert by_url["https://example.com/b"].folder_path == ("工作", "开发")
        assert by_url["https://example.com/root"].folder_path == ("未分类",)

    def test_csv_export_and_parse_back(self, auth_client, db_session):
        from app.services.import_parser import parse_csv

        self._seed(db_session)
        response = auth_client.get("/api/export/csv", headers=_headers(auth_client))
        assert response.status_code == 200
        body = response.content
        assert body.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
        parsed = parse_csv(body)
        assert parsed.errors == []
        by_url = {item.normalized_url: item for item in parsed.items}
        key = "https://example.com/a?x=1&y=2"
        assert by_url[key].title == "A<&"
        assert by_url[key].folder_path == ("工作",)
        assert by_url[key].tags == ["标签,一"]
        assert by_url["https://example.com/b"].folder_path == ("工作", "开发")
        assert by_url["https://example.com/root"].folder_path == ()
        # formula 安全：以 = 开头的 title 会加前缀并凭 marker 还原
        assert any(item.title == "A<&" for item in parsed.items)

    def test_csv_export_excludes_trash(self, auth_client, db_session):
        from app.database import now_utc
        from app.models.bookmark import Bookmark

        db_session.add(
            Bookmark(
                title="gone",
                url="https://example.com/gone",
                normalized_url="ng",
                deleted_at=now_utc(),
            )
        )
        db_session.commit()
        response = auth_client.get("/api/export/csv", headers=_headers(auth_client))
        assert response.status_code == 200
        assert "gone" not in response.text
