"""分类管理 API 测试（BM-V1-401~405）。"""

from __future__ import annotations

import pytest
from app.models.bookmark import Bookmark
from app.models.category import Category
from app.services.versioned import get_tree_revision

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


def _fresh_category(db_session, category_id: int):
    """绕过 identity map 读取最新状态。"""
    db_session.expire_all()
    return db_session.get(Category, category_id)


def _rev(db_session) -> int:
    return get_tree_revision(db_session)


class TestCreateUpdate:
    def test_create_root_and_child(self, auth_client, db_session):
        response = auth_client.post(
            "/api/categories",
            json={"name": "工作", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers={"X-CSRF-Token": _csrf(auth_client)},
        )
        assert response.status_code == 200
        root_id = response.json()["id"]
        response = auth_client.post(
            "/api/categories",
            json={"name": "开发", "parent_id": root_id, "category_tree_revision": _rev(db_session)},
            headers={"X-CSRF-Token": _csrf(auth_client)},
        )
        assert response.status_code == 200
        assert _fresh_category(db_session, root_id) is not None

    def test_create_same_name_conflict(self, auth_client, db_session):
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        auth_client.post(
            "/api/categories",
            json={"name": "AI", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers=headers,
        )
        response = auth_client.post(
            "/api/categories",
            json={"name": "ai", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers=headers,
        )
        assert response.status_code == 409

    def test_update_name_and_parent_together(self, auth_client, db_session):
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        first = auth_client.post(
            "/api/categories",
            json={"name": "A", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        second = auth_client.post(
            "/api/categories",
            json={"name": "B", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        child = auth_client.post(
            "/api/categories",
            json={"name": "C", "parent_id": first, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        cat = _fresh_category(db_session, child)
        response = auth_client.put(
            f"/api/categories/{child}",
            json={
                "name": "C2",
                "parent_id": second,
                "version": cat.version,
                "category_tree_revision": _rev(db_session),
            },
            headers=headers,
        )
        assert response.status_code == 200
        moved = _fresh_category(db_session, child)
        assert moved.name == "C2"
        assert moved.parent_id == second

    def test_update_move_into_own_descendant_rejected(self, auth_client, db_session):
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        root = auth_client.post(
            "/api/categories",
            json={"name": "A", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        child = auth_client.post(
            "/api/categories",
            json={"name": "B", "parent_id": root, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        cat = _fresh_category(db_session, root)
        response = auth_client.put(
            f"/api/categories/{root}",
            json={
                "name": "A",
                "parent_id": child,
                "version": cat.version,
                "category_tree_revision": _rev(db_session),
            },
            headers=headers,
        )
        assert response.status_code == 409

    def test_update_stale_revision_rejected(self, auth_client, db_session):
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        root = auth_client.post(
            "/api/categories",
            json={"name": "A", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        cat = _fresh_category(db_session, root)
        response = auth_client.put(
            f"/api/categories/{root}",
            json={
                "name": "A2",
                "parent_id": None,
                "version": cat.version,
                "category_tree_revision": _rev(db_session) + 5,
            },
            headers=headers,
        )
        assert response.status_code == 409
        assert _fresh_category(db_session, root).name == "A"


class TestDelete:
    def _make(self, auth_client, db_session):
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        root = auth_client.post(
            "/api/categories",
            json={"name": "工作", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        child = auth_client.post(
            "/api/categories",
            json={"name": "开发", "parent_id": root, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        # 直接书签挂在被删除节点上（删除时需迁移）；挂子节点的书签不受影响
        db_session.add(
            Bookmark(
                title="direct",
                url="https://example.com/direct",
                normalized_url="https://example.com/direct",
                category_id=root,
            )
        )
        db_session.add(
            Bookmark(
                title="nested",
                url="https://example.com/nested",
                normalized_url="https://example.com/nested",
                category_id=child,
            )
        )
        db_session.commit()
        return root, child

    def test_delete_confirm_info(self, auth_client, db_session):
        root, child = self._make(auth_client, db_session)
        response = auth_client.get(
            f"/api/categories/{root}/delete-info", headers={"Accept": "text/html"}
        )
        assert response.status_code == 200
        assert "删除该分类" in response.text
        # 删除确认 fragment 提供 version/revision 数据
        assert f'data-category-id="{root}"' in response.text

    def test_delete_moves_bookmarks_and_promotes_child(self, auth_client, db_session):
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        root, child = self._make(auth_client, db_session)
        cat = _fresh_category(db_session, root)
        response = auth_client.request(
            "DELETE",
            f"/api/categories/{root}",
            json={
                "version": cat.version,
                "category_tree_revision": _rev(db_session),
                "move_bookmarks_to_category_id": None,
            },
            headers=headers,
        )
        assert response.status_code == 200
        # 子分类提升为根；被删节点的直接书签迁移到未分类；子节点书签不受影响
        promoted = _fresh_category(db_session, child)
        assert promoted.parent_id is None
        db_session.expire_all()
        direct = db_session.query(Bookmark).filter(Bookmark.title == "direct").first()
        nested = db_session.query(Bookmark).filter(Bookmark.title == "nested").first()
        assert direct.category_id is None
        assert nested.category_id == child

    def test_delete_info_page_and_page_render(self, auth_client):
        response = auth_client.get("/categories", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert "新建根分类" in response.text


class TestReorder:
    def test_reorder_siblings_via_api(self, auth_client, db_session):
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        ids = []
        for name in ("A", "B", "C"):
            response = auth_client.post(
                "/api/categories",
                json={"name": name, "parent_id": None, "category_tree_revision": _rev(db_session)},
                headers=headers,
            )
            ids.append(response.json()["id"])
        cats = {cid: _fresh_category(db_session, cid) for cid in ids}
        ordered = [{"id": cid, "version": cats[cid].version} for cid in reversed(ids)]
        response = auth_client.post(
            "/api/categories/reorder",
            json={
                "parent_id": None,
                "ordered": ordered,
                "category_tree_revision": _rev(db_session),
            },
            headers=headers,
        )
        assert response.status_code == 200
        refreshed = {cid: _fresh_category(db_session, cid).sort_order for cid in ids}
        assert refreshed[ids[2]] < refreshed[ids[1]] < refreshed[ids[0]]

    def test_reorder_cross_parent_rejected(self, auth_client, db_session):
        headers = {"X-CSRF-Token": _csrf(auth_client)}
        root = auth_client.post(
            "/api/categories",
            json={"name": "R", "parent_id": None, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        child = auth_client.post(
            "/api/categories",
            json={"name": "C", "parent_id": root, "category_tree_revision": _rev(db_session)},
            headers=headers,
        ).json()["id"]
        cat = _fresh_category(db_session, child)
        response = auth_client.post(
            "/api/categories/reorder",
            json={
                "parent_id": None,
                "ordered": [{"id": child, "version": cat.version}],
                "category_tree_revision": _rev(db_session),
            },
            headers=headers,
        )
        assert response.status_code == 409
