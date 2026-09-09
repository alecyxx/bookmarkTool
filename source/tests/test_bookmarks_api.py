"""书签核心 API 测试（BM-V1-301~307）。"""

from __future__ import annotations

import pytest
from app.models.bookmark import Bookmark
from app.services.versioned import get_tree_revision

PASSWORD = "strong-password-1234"
URL_A = "https://example.com/docs/page"


def _csrf(client) -> str:
    return client.cookies.get("bookmark_csrf", "")


@pytest.fixture
def auth_client(client, make_admin):
    make_admin(password=PASSWORD)
    client.get("/login")
    client.post(
        "/auth/login",
        data={"username": "admin", "password": PASSWORD, "csrf_token": _csrf(client)},
    )
    return client


def _post(client, url: str, payload: dict):
    return client.post(url, json=payload, headers={"X-CSRF-Token": _csrf(client)})


def _put(client, url: str, payload: dict):
    return client.put(url, json=payload, headers={"X-CSRF-Token": _csrf(client)})


def _fresh_bookmark(db_session, bookmark_id: int) -> Bookmark:
    """绕过 identity map 读取数据库最新状态。"""
    db_session.expire_all()
    return db_session.get(Bookmark, bookmark_id)  # noqa: PLC0415 - 保持原样调用


def _payload(**overrides) -> dict:
    base = {
        "title": "示例页面",
        "url": URL_A,
        "description": "",
        "category_id": None,
        "tags": [],
        "is_favorite": False,
    }
    base.update(overrides)
    return base


class TestCreate:
    def test_create_with_tags_and_favorite(self, auth_client, db_session):
        response = _post(
            auth_client,
            "/api/bookmarks",
            _payload(
                title="ChatGPT", url="https://chatgpt.com/", tags=["AI", "GPT"], is_favorite=True
            ),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["ok"] is True
        assert body["duplicate_active"] == 0
        bookmark = _fresh_bookmark(db_session, body["id"])
        assert bookmark is not None
        assert bookmark.normalized_url == "https://chatgpt.com/"
        assert bookmark.is_favorite is True
        assert sorted(t.name for t in bookmark.tags) == ["AI", "GPT"]

    def test_create_uncategorized(self, auth_client, db_session):
        response = _post(auth_client, "/api/bookmarks", _payload(title="无分类"))
        assert response.status_code == 201
        bookmark = _fresh_bookmark(db_session, response.json()["id"])
        assert bookmark.category_id is None

    def test_create_with_category(self, auth_client, db_session):
        from app.services.category_service import CategoryService

        category = CategoryService(db_session).create("开发", None, get_tree_revision(db_session))
        db_session.commit()
        response = _post(
            auth_client,
            "/api/bookmarks",
            _payload(title="FastAPI", url="https://fastapi.tiangolo.com", category_id=category.id),
        )
        assert response.status_code == 201
        assert _fresh_bookmark(db_session, response.json()["id"]).category_id == category.id

    def test_create_invalid_url_rejected(self, auth_client):
        response = _post(auth_client, "/api/bookmarks", _payload(url="javascript:alert(1)"))
        assert response.status_code == 422
        body = response.json()
        assert "url" in body["error"]["field_errors"]

    def test_create_missing_category_rejected(self, auth_client):
        response = _post(auth_client, "/api/bookmarks", _payload(category_id=424242))
        assert response.status_code == 422

    def test_create_duplicate_url_not_blocked(self, auth_client, db_session):
        first = _post(auth_client, "/api/bookmarks", _payload(title="第一条"))
        assert first.status_code == 201
        second = _post(auth_client, "/api/bookmarks", _payload(title="第二条"))
        assert second.status_code == 201
        body = second.json()
        assert body["duplicate_active"] == 1  # 提示但不阻止
        assert db_session.query(Bookmark).count() == 2

    def test_create_duplicate_url_with_trash(self, auth_client, db_session):
        first = _post(auth_client, "/api/bookmarks", _payload(title="将删除"))
        bookmark = _fresh_bookmark(db_session, first.json()["id"])
        from app.database import now_utc

        bookmark.deleted_at = now_utc()
        db_session.commit()
        second = _post(auth_client, "/api/bookmarks", _payload(title="回收站冲突"))
        assert second.status_code == 201
        assert second.json()["duplicate_trashed"] == 1

    def test_requires_login_and_csrf(self, client, make_admin):
        make_admin(password=PASSWORD)
        client.get("/login")
        csrf = _csrf(client)
        # 未登录 JSON（携带有效 CSRF Cookie/Token）-> 401
        response = client.post("/api/bookmarks", json=_payload(), headers={"X-CSRF-Token": csrf})
        assert response.status_code == 401
        # 登录后但无 CSRF 头 -> 403
        client.post(
            "/auth/login",
            data={"username": "admin", "password": PASSWORD, "csrf_token": csrf},
        )
        response = client.post("/api/bookmarks", json=_payload())
        assert response.status_code == 403
        # 登录且携带 CSRF 头 -> 成功
        response = client.post(
            "/api/bookmarks", json=_payload(title="ok"), headers={"X-CSRF-Token": _csrf(client)}
        )
        assert response.status_code == 201


class TestReadAndEdit:
    def _create_one(self, auth_client) -> int:
        response = _post(auth_client, "/api/bookmarks", _payload(title="原始标题", tags=["A"]))
        return response.json()["id"]

    def test_edit_form_returns_prefilled_html(self, auth_client):
        bookmark_id = self._create_one(auth_client)
        response = auth_client.get(f"/api/bookmarks/{bookmark_id}")
        assert response.status_code == 200
        assert "原始标题" in response.text
        assert 'value="https://example.com/docs/page"' in response.text

    def test_edit_missing_returns_404(self, auth_client):
        response = auth_client.get("/api/bookmarks/424242")
        assert response.status_code == 404

    def test_update_success_keeps_unsubmitted_fields(self, auth_client, db_session):
        bookmark_id = self._create_one(auth_client)
        before_version = _fresh_bookmark(db_session, bookmark_id).version
        response = _put(
            auth_client,
            f"/api/bookmarks/{bookmark_id}?version={before_version}",
            _payload(title="新标题", url=URL_A, tags=["A", "B"]),
        )
        assert response.status_code == 200
        fresh = _fresh_bookmark(db_session, bookmark_id)
        assert fresh.title == "新标题"
        assert fresh.version == before_version + 1
        assert sorted(t.name for t in fresh.tags) == ["A", "B"]

    def test_update_stale_version_409(self, auth_client, db_session):
        bookmark_id = self._create_one(auth_client)
        bookmark = _fresh_bookmark(db_session, bookmark_id)
        # 第一个编辑者成功
        assert (
            _put(
                auth_client,
                f"/api/bookmarks/{bookmark_id}?version={bookmark.version}",
                _payload(title="第一个"),
            ).status_code
            == 200
        )
        # 第二个编辑者用旧版本 -> 409
        response = _put(
            auth_client,
            f"/api/bookmarks/{bookmark_id}?version={bookmark.version}",
            _payload(title="第二个"),
        )
        assert response.status_code == 409
        assert _fresh_bookmark(db_session, bookmark_id).title == "第一个"

    def test_update_tags_replaced(self, auth_client, db_session):
        bookmark_id = self._create_one(auth_client)
        bookmark = _fresh_bookmark(db_session, bookmark_id)
        response = _put(
            auth_client,
            f"/api/bookmarks/{bookmark_id}?version={bookmark.version}",
            _payload(title="原始标题", url=URL_A, tags=["C"]),
        )
        assert response.status_code == 200
        assert [t.name for t in _fresh_bookmark(db_session, bookmark_id).tags] == ["C"]

    def test_update_category_cleared(self, auth_client, db_session):
        from app.services.category_service import CategoryService

        category = CategoryService(db_session).create("工作", None, get_tree_revision(db_session))
        db_session.commit()
        response = _post(
            auth_client,
            "/api/bookmarks",
            _payload(title="t", url="https://x.com/a", category_id=category.id),
        )
        bookmark_id = response.json()["id"]
        bookmark = _fresh_bookmark(db_session, bookmark_id)
        assert bookmark.category_id == category.id
        response = _put(
            auth_client,
            f"/api/bookmarks/{bookmark_id}?version={bookmark.version}",
            _payload(title="t", url="https://x.com/a", category_id=None),
        )
        assert response.status_code == 200
        assert _fresh_bookmark(db_session, bookmark_id).category_id is None


class TestFavoriteAndSoftDelete:
    def _create_one(self, auth_client) -> int:
        return _post(auth_client, "/api/bookmarks", _payload(title="t")).json()["id"]

    def test_favorite_toggle(self, auth_client, db_session):
        bookmark_id = self._create_one(auth_client)
        bookmark = _fresh_bookmark(db_session, bookmark_id)
        response = _put(
            auth_client,
            f"/api/bookmarks/{bookmark_id}/favorite",
            {"version": bookmark.version, "is_favorite": True},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["is_favorite"] is True
        assert body["version"] == bookmark.version + 1

    def test_favorite_stale_version_409(self, auth_client, db_session):
        bookmark_id = self._create_one(auth_client)
        bookmark = _fresh_bookmark(db_session, bookmark_id)
        response = _put(
            auth_client,
            f"/api/bookmarks/{bookmark_id}/favorite",
            {"version": bookmark.version + 99, "is_favorite": True},
        )
        assert response.status_code == 409

    def test_soft_delete_moves_to_trash_only(self, auth_client, db_session):
        bookmark_id = self._create_one(auth_client)
        bookmark = _fresh_bookmark(db_session, bookmark_id)
        bookmark.tags = []
        db_session.commit()
        response = auth_client.delete(
            f"/api/bookmarks/{bookmark_id}?version={bookmark.version}",
            headers={"X-CSRF-Token": _csrf(auth_client)},
        )
        assert response.status_code == 200
        fresh = _fresh_bookmark(db_session, bookmark_id)
        assert fresh.deleted_at is not None  # 软删除
        assert fresh.title == "t"  # 记录仍在（保留标签/分类/时间）

    def test_normal_list_excludes_deleted(self, auth_client, db_session):
        self._create_one(auth_client)
        bookmark_id = self._create_one(auth_client)
        bookmark = _fresh_bookmark(db_session, bookmark_id)
        auth_client.delete(
            f"/api/bookmarks/{bookmark_id}?version={bookmark.version}",
            headers={"X-CSRF-Token": _csrf(auth_client)},
        )
        page = auth_client.get("/bookmarks", headers={"Accept": "text/html"})
        assert page.status_code == 200
        assert "共 1 条" in page.text

    def test_delete_version_conflict_on_first_delete(self, auth_client, db_session):
        bookmark_id = self._create_one(auth_client)
        bookmark = _fresh_bookmark(db_session, bookmark_id)
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        # 首次删除携带过期版本 -> 409 且未删除
        response = auth_client.delete(
            f"/api/bookmarks/{bookmark_id}?version={bookmark.version + 99}", headers=headers
        )
        assert response.status_code == 409
        assert _fresh_bookmark(db_session, bookmark_id).deleted_at is None
        # 正确版本删除成功；再次删除幂等成功
        assert (
            auth_client.delete(
                f"/api/bookmarks/{bookmark_id}?version={bookmark.version}", headers=headers
            ).status_code
            == 200
        )
        assert (
            auth_client.delete(
                f"/api/bookmarks/{bookmark_id}?version={bookmark.version}", headers=headers
            ).status_code
            == 200
        )
