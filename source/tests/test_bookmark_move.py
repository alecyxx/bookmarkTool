"""书签自定义排序（上移/下移，按分类独立权重）测试。"""

from __future__ import annotations

import pytest
from app.models.bookmark import Bookmark
from app.services.category_service import CategoryService
from app.services.url_service import normalize_url
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


def _category(db_session):
    svc = CategoryService(db_session)
    cat = svc.create("工作", None, get_tree_revision(db_session))
    db_session.flush()
    return cat


def _add_bookmark(db_session, category, title, weight, index):
    url = f"https://example.com/{index}"
    bookmark = Bookmark(
        title=title,
        url=url,
        normalized_url=normalize_url(url),
        description="",
        category_id=category.id,
        sort_weight=weight,
    )
    db_session.add(bookmark)
    db_session.flush()
    return bookmark


def _csrf(auth_client) -> str:
    return auth_client.cookies.get("bookmark_csrf", "")


class TestMove:
    def test_move_up_swaps_weights(self, auth_client, db_session):
        cat = _category(db_session)
        b1 = _add_bookmark(db_session, cat, "A", 0, 1)
        b2 = _add_bookmark(db_session, cat, "B", 1, 2)
        db_session.commit()

        resp = auth_client.post(
            "/api/bookmarks/move",
            json={"id": b2.id, "version": b2.version, "direction": "up"},
            headers={"X-CSRF-Token": _csrf(auth_client)},
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert (db_session.get(Bookmark, b1.id).sort_weight, db_session.get(Bookmark, b2.id).sort_weight) == (1, 0)

    def test_move_up_at_top_conflict(self, auth_client, db_session):
        cat = _category(db_session)
        b1 = _add_bookmark(db_session, cat, "A", 0, 1)
        _add_bookmark(db_session, cat, "B", 1, 2)
        db_session.commit()

        resp = auth_client.post(
            "/api/bookmarks/move",
            json={"id": b1.id, "version": b1.version, "direction": "up"},
            headers={"X-CSRF-Token": _csrf(auth_client)},
        )
        assert resp.status_code == 409
        assert "已是最前" in resp.json()["error"]["message"]

    def test_move_stale_version_conflict(self, auth_client, db_session):
        cat = _category(db_session)
        _add_bookmark(db_session, cat, "A", 0, 1)
        b2 = _add_bookmark(db_session, cat, "B", 1, 2)
        db_session.commit()

        resp = auth_client.post(
            "/api/bookmarks/move",
            json={"id": b2.id, "version": b2.version + 1, "direction": "up"},
            headers={"X-CSRF-Token": _csrf(auth_client)},
        )
        assert resp.status_code == 409

    def test_move_down_swaps_weights(self, auth_client, db_session):
        cat = _category(db_session)
        b1 = _add_bookmark(db_session, cat, "A", 0, 1)
        b2 = _add_bookmark(db_session, cat, "B", 1, 2)
        db_session.commit()

        resp = auth_client.post(
            "/api/bookmarks/move",
            json={"id": b1.id, "version": b1.version, "direction": "down"},
            headers={"X-CSRF-Token": _csrf(auth_client)},
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert (db_session.get(Bookmark, b1.id).sort_weight, db_session.get(Bookmark, b2.id).sort_weight) == (1, 0)


class TestCustomSortPage:
    def test_custom_sort_renders_move_buttons(self, auth_client, db_session):
        cat = _category(db_session)
        _add_bookmark(db_session, cat, "A", 0, 1)
        _add_bookmark(db_session, cat, "B", 1, 2)
        db_session.commit()

        resp = auth_client.get(
            f"/bookmarks?sort=custom&category={cat.id}", headers={"Accept": "text/html"}
        )
        assert resp.status_code == 200
        assert 'data-bm-move' in resp.text
        assert "自定义" in resp.text  # 排序下拉选项

    def test_custom_sort_hides_move_buttons_without_category(self, auth_client, db_session):
        cat = _category(db_session)
        _add_bookmark(db_session, cat, "A", 0, 1)
        db_session.commit()

        resp = auth_client.get("/bookmarks?sort=custom", headers={"Accept": "text/html"})
        assert resp.status_code == 200
        assert 'data-bm-move' not in resp.text  # 非分类视图不显示移动按钮
