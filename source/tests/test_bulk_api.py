"""批量操作与回收站测试（BM-V1-502~507）。"""

from __future__ import annotations

import pytest
from app.models.bookmark import Bookmark
from app.models.tag import Tag
from sqlalchemy import select

PASSWORD = "strong-password-1234"


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


def _csrf(client) -> str:
    return client.cookies.get("bookmark_csrf", "")


def _headers(auth_client) -> dict:
    return {"X-CSRF-Token": _csrf(auth_client)}


def _make_bookmarks(db_session, count: int = 3):
    """创建 count 个正常书签，返回 [(id, version)]。"""
    result = []
    for index in range(count):
        bookmark = Bookmark(
            title=f"b{index}",
            url=f"https://example.com/{index}",
            normalized_url=f"https://example.com/{index}",
        )
        db_session.add(bookmark)
        db_session.flush()
        result.append((bookmark.id, bookmark.version))
    db_session.commit()
    return result


def _versions(db_session) -> dict[int, int]:
    """列查询（不经 ORM 身份映射）读取数据库最新版本。"""
    return {row[0]: row[1] for row in db_session.execute(select(Bookmark.id, Bookmark.version))}


class TestBulkValidation:
    def test_empty_items_rejected(self, auth_client):
        response = auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": []}, headers=_headers(auth_client)
        )
        assert response.status_code == 422

    def test_duplicate_items_rejected(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 1)
        payload = {"items": [list(items[0]), list(items[0])]}
        response = auth_client.post(
            "/api/bookmarks/bulk-delete", json=payload, headers=_headers(auth_client)
        )
        assert response.status_code == 422

    def test_501_items_rejected(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 1)
        big = [list(items[0])] * 501
        response = auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": big}, headers=_headers(auth_client)
        )
        assert response.status_code == 422

    def test_missing_id_all_rolls_back(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 2)
        bad = [list(items[0]), [999999, 1]]
        response = auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": bad}, headers=_headers(auth_client)
        )
        assert response.status_code == 404
        from sqlalchemy import select

        assert (
            db_session.scalar(select(Bookmark.id).where(Bookmark.deleted_at.is_not(None))) is None
        )

    def test_stale_version_rolls_back_everything(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 2)
        bad = [list(items[0]), [items[1][0], items[1][1] + 99]]
        response = auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": bad}, headers=_headers(auth_client)
        )
        assert response.status_code == 409
        remaining = db_session.execute(
            select(Bookmark.id).where(Bookmark.deleted_at.is_not(None))
        ).all()
        assert remaining == []


class TestBulkCommands:
    def test_bulk_soft_delete(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 3)
        response = auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": items}, headers=_headers(auth_client)
        )
        assert response.status_code == 200
        assert response.json()["count"] == 3
        deleted = db_session.execute(
            select(Bookmark.id).where(Bookmark.deleted_at.is_not(None))
        ).all()
        assert len(deleted) == 3  # 全部进入回收站，无物理删除

    def test_bulk_set_category(self, auth_client, db_session):
        from app.services.category_service import CategoryService
        from app.services.versioned import get_tree_revision

        svc = CategoryService(db_session)
        category = svc.create("开发", None, get_tree_revision(db_session))
        db_session.commit()
        items = _make_bookmarks(db_session, 2)
        response = auth_client.post(
            "/api/bookmarks/bulk-category",
            json={"items": items, "category_id": category.id},
            headers=_headers(auth_client),
        )
        assert response.status_code == 200
        versions = _versions(db_session)
        assert all(db_session.get(Bookmark, bid).category_id == category.id for bid, _ in items)
        # 再批量置为未分类
        response = auth_client.post(
            "/api/bookmarks/bulk-category",
            json={"items": [(bid, versions[bid]) for bid, _ in items], "category_id": None},
            headers=_headers(auth_client),
        )
        assert response.status_code == 200
        assert all(db_session.get(Bookmark, bid).category_id is None for bid, _ in items)

    def test_bulk_add_tags_only_adds(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 2)
        response = auth_client.post(
            "/api/bookmarks/bulk-tags",
            json={"items": items, "tags": ["Python", "AI"]},
            headers=_headers(auth_client),
        )
        assert response.status_code == 200
        versions = _versions(db_session)
        # 再次添加含已有标签 -> 不重复
        response = auth_client.post(
            "/api/bookmarks/bulk-tags",
            json={
                "items": [(bid, versions[bid]) for bid, _ in items],
                "tags": ["Python", "Docker"],
            },
            headers=_headers(auth_client),
        )
        assert response.status_code == 200
        bookmark = db_session.get(Bookmark, items[0][0])
        assert sorted(tag.name for tag in bookmark.tags) == ["AI", "Docker", "Python"]

    def test_bulk_delete_includes_trashed_item_conflict(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 2)
        # 先把第一个软删
        auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": [items[0]]}, headers=_headers(auth_client)
        )
        versions = _versions(db_session)
        bad = [(items[0][0], versions[items[0][0]]), list(items[1])]
        response = auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": bad}, headers=_headers(auth_client)
        )
        assert response.status_code == 409  # 已删除项阻止整批

        # 第二个未被删除（整体回滚）
        assert db_session.get(Bookmark, items[1][0]).deleted_at is None


class TestTrashLifecycle:
    def _trash_one(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 1)
        auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": items}, headers=_headers(auth_client)
        )
        return items[0]

    def test_trash_page_lists_and_excludes_normal(self, auth_client, db_session):
        bookmark_id, version = self._trash_one(auth_client, db_session)
        page = auth_client.get("/trash", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert "共 1 条" in page.text
        assert "回收站是空的" not in page.text

    def test_restore_single_keeps_fields(self, auth_client, db_session):

        tag = Tag(name="保留", normalized_name="保留")
        db_session.add(tag)
        db_session.flush()
        items = _make_bookmarks(db_session, 1)
        bookmark = db_session.get(Bookmark, items[0][0])
        bookmark.tags.append(tag)
        db_session.commit()
        auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": [items[0]]}, headers=_headers(auth_client)
        )
        versions = _versions(db_session)
        response = auth_client.post(
            f"/api/bookmarks/{items[0][0]}/restore",
            json={"version": versions[items[0][0]]},
            headers=_headers(auth_client),
        )
        assert response.status_code == 200, response.text
        restored = db_session.get(Bookmark, items[0][0])
        assert restored.deleted_at is None
        assert [t.name for t in restored.tags] == ["保留"]

    def test_restore_duplicate_warns_but_allowed(self, auth_client, db_session):
        first = _make_bookmarks(db_session, 1)[0]
        auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": [first]}, headers=_headers(auth_client)
        )
        # 正常列表再添加相同 URL
        duplicate = Bookmark(
            title="same", url="https://example.com/0", normalized_url="https://example.com/0"
        )
        db_session.add(duplicate)
        db_session.commit()
        # 回收站列表行展示重复提示（恢复不阻止，仅提示）
        page = auth_client.get("/trash", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert "恢复后将与" in page.text
        versions = _versions(db_session)
        response = auth_client.post(
            f"/api/bookmarks/{first[0]}/restore",
            json={"version": versions[first[0]]},
            headers=_headers(auth_client),
        )
        assert response.status_code == 200  # 允许恢复（允许重复，仅提示）
        assert db_session.query(Bookmark).count() == 2

    def test_permanent_delete_only_trash(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 2)
        versions = _versions(db_session)
        # 正常书签无法永久删除
        response = auth_client.request(
            "DELETE",
            f"/api/bookmarks/{items[0][0]}/permanent",
            json={"version": versions[items[0][0]]},
            headers=_headers(auth_client),
        )
        assert response.status_code == 409
        # 先软删再永久删除
        auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": items}, headers=_headers(auth_client)
        )
        versions = _versions(db_session)
        response = auth_client.request(
            "DELETE",
            f"/api/bookmarks/{items[0][0]}/permanent",
            json={"version": versions[items[0][0]]},
            headers=_headers(auth_client),
        )
        assert response.status_code == 200
        assert db_session.get(Bookmark, items[0][0]) is None  # 物理删除
        assert db_session.get(Bookmark, items[1][0]) is not None

    def test_bulk_permanent_delete(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 3)
        auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": items}, headers=_headers(auth_client)
        )
        versions = _versions(db_session)
        response = auth_client.post(
            "/api/bookmarks/bulk-permanent-delete",
            json={"items": [(bid, versions[bid]) for bid, _ in items]},
            headers=_headers(auth_client),
        )
        assert response.status_code == 200
        assert db_session.query(Bookmark).count() == 0

    def test_empty_trash_requires_confirmation_word(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 2)
        auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": items}, headers=_headers(auth_client)
        )
        # 错误确认词拒绝
        response = auth_client.post(
            "/api/trash/empty", json={"confirm": "随便输入"}, headers=_headers(auth_client)
        )
        assert response.status_code == 422
        assert db_session.query(Bookmark).count() == 2
        # 正确确认词清空
        response = auth_client.post(
            "/api/trash/empty", json={"confirm": "清空回收站"}, headers=_headers(auth_client)
        )
        assert response.status_code == 200
        assert response.json()["count"] == 2
        assert db_session.query(Bookmark).count() == 0

    def test_trash_search(self, auth_client, db_session):
        items = _make_bookmarks(db_session, 2)
        auth_client.post(
            "/api/bookmarks/bulk-delete", json={"items": items}, headers=_headers(auth_client)
        )
        page = auth_client.get("/trash?q=b0", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert "共 1 条" in page.text

    def test_trash_item_actions_render(self, auth_client, db_session):
        self._trash_one(auth_client, db_session)
        page = auth_client.get("/trash", headers={"Accept": "text/html"})
        assert "data-trash-restore" in page.text
        assert "data-trash-permanent" in page.text
        assert "选择当前页全部" in page.text
        assert "data-bulk-action" in page.text
