"""标签管理 API 与页面测试（BM-V1-407/408）。"""

from __future__ import annotations

import pytest
from app.models.bookmark import Bookmark
from app.models.tag import Tag

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


def _fresh_tag(db_session, tag_id: int):
    """绕过 identity map 读取最新状态。"""
    db_session.expire_all()
    return db_session.get(Tag, tag_id)


def _headers(auth_client) -> dict:
    return {"X-CSRF-Token": _csrf(auth_client)}


class TestTagCrud:
    def test_create_and_page(self, auth_client):
        headers = _headers(auth_client)
        response = auth_client.post("/api/tags", json={"name": "Python"}, headers=headers)
        assert response.status_code == 200
        page = auth_client.get("/tags", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert "Python" in page.text
        assert "使用次数" in page.text

    def test_create_duplicate_normalized_conflict(self, auth_client):
        headers = _headers(auth_client)
        auth_client.post("/api/tags", json={"name": "AI"}, headers=headers)
        response = auth_client.post("/api/tags", json={"name": "ＡＩ"}, headers=headers)
        assert response.status_code == 409

    def test_rename_and_conflict(self, auth_client, db_session):
        headers = _headers(auth_client)
        first = auth_client.post("/api/tags", json={"name": "Git"}, headers=headers).json()["id"]
        second = auth_client.post("/api/tags", json={"name": "Docker"}, headers=headers).json()[
            "id"
        ]
        tag = _fresh_tag(db_session, first)
        # 改名为已存在名称 -> 409 不合并
        response = auth_client.put(
            f"/api/tags/{first}",
            json={"name": "docker", "version": tag.version},
            headers=headers,
        )
        assert response.status_code == 409
        # 正常改名
        response = auth_client.put(
            f"/api/tags/{first}",
            json={"name": "GitHub", "version": tag.version},
            headers=headers,
        )
        assert response.status_code == 200
        assert _fresh_tag(db_session, first).name == "GitHub"
        # 空名称 422
        tag = _fresh_tag(db_session, second)
        response = auth_client.put(
            f"/api/tags/{second}",
            json={"name": "", "version": tag.version},
            headers=headers,
        )
        assert response.status_code == 422

    def test_delete_keeps_bookmarks(self, auth_client, db_session):
        headers = _headers(auth_client)
        tag_id = auth_client.post("/api/tags", json={"name": "Temp"}, headers=headers).json()["id"]
        tag = _fresh_tag(db_session, tag_id)
        bookmark = Bookmark(
            title="b",
            url="https://example.com/x",
            normalized_url="https://example.com/x",
        )
        db_session.add(bookmark)
        db_session.flush()
        bookmark.tags.append(tag)
        db_session.commit()
        # 删除确认信息显示受影响书签数
        info = auth_client.get(f"/api/tags/{tag_id}/delete-info", headers={"Accept": "text/html"})
        assert info.status_code == 200
        assert "1" in info.text
        response = auth_client.request(
            "DELETE",
            f"/api/tags/{tag_id}",
            json={"version": tag.version},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["affected_bookmarks"] == 1
        assert _fresh_tag(db_session, tag_id) is None
        assert db_session.query(Bookmark).count() == 1  # 书签保留

    def test_delete_stale_version_409(self, auth_client, db_session):
        headers = _headers(auth_client)
        tag_id = auth_client.post("/api/tags", json={"name": "X"}, headers=headers).json()["id"]
        response = auth_client.request(
            "DELETE",
            f"/api/tags/{tag_id}",
            json={"version": 99},
            headers=headers,
        )
        assert response.status_code == 409
        assert _fresh_tag(db_session, tag_id) is not None


class TestTagCounts:
    def test_counts_exclude_trash(self, auth_client, db_session):
        headers = _headers(auth_client)
        tag_id = auth_client.post("/api/tags", json={"name": "计数"}, headers=headers).json()["id"]
        tag = _fresh_tag(db_session, tag_id)
        from app.database import now_utc

        alive = Bookmark(title="a", url="https://a.com", normalized_url="n1")
        trashed = Bookmark(
            title="t", url="https://t.com", normalized_url="n2", deleted_at=now_utc()
        )
        db_session.add_all([alive, trashed])
        db_session.flush()
        for b in (alive, trashed):
            b.tags.append(tag)
        db_session.commit()
        page = auth_client.get("/tags", headers={"Accept": "text/html"})
        assert "计数" in page.text
        # 列表 fragment 中只计未删除（计数 1）
        fragment = auth_client.get("/api/tags/list", headers={"Accept": "text/html"})
        assert "1" in fragment.text

    def test_bookmarks_tag_filter_uses_id_after_rename(self, auth_client, db_session):
        """标签改名后 URL 使用 ID 仍有效（书签页筛选不受名称影响）。"""
        headers = _headers(auth_client)
        tag_id = auth_client.post("/api/tags", json={"name": "旧名"}, headers=headers).json()["id"]
        bookmark = Bookmark(title="b", url="https://b.com", normalized_url="n3")
        db_session.add(bookmark)
        db_session.flush()
        bookmark.tags.append(_fresh_tag(db_session, tag_id))
        db_session.commit()
        tag = _fresh_tag(db_session, tag_id)
        auth_client.put(
            f"/api/tags/{tag_id}",
            json={"name": "新名", "version": tag.version},
            headers=headers,
        )
        page = auth_client.get(f"/bookmarks?tag={tag_id}", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert "共 1 条" in page.text
        assert "新名" in page.text
