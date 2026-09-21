"""书签页面 URL 状态测试（BM-V1-306）。"""

from __future__ import annotations

import re

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


def _seed(auth_client):
    for index in range(3):
        auth_client.post(
            "/api/bookmarks",
            json={
                "title": f"标题 {index}",
                "url": f"https://example.com/{index}",
                "description": "",
                "category_id": None,
                "tags": [f"tag{index}"],
                "is_favorite": index == 0,
            },
            headers={"X-CSRF-Token": auth_client.cookies.get("bookmark_csrf", "")},
        )


class TestPageUrlState:
    def test_search_parameter_roundtrip(self, auth_client):
        _seed(auth_client)
        response = auth_client.get(
            "/bookmarks?q=%E6%A0%87%E9%A2%98%201", headers={"Accept": "text/html"}
        )
        assert response.status_code == 200
        assert "标题 1" in response.text
        assert "共 1 条" in response.text

    def test_favorite_filter_page(self, auth_client):
        _seed(auth_client)
        response = auth_client.get("/bookmarks?favorite=1", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert "共 1 条" in response.text

    def test_invalid_sort_falls_back(self, auth_client):
        _seed(auth_client)
        response = auth_client.get("/bookmarks?sort=bogus", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert "共 3 条" in response.text

    def test_invalid_category_returns_empty_not_500(self, auth_client):
        _seed(auth_client)
        response = auth_client.get("/bookmarks?category=99999", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert "没有符合条件的书签" in response.text

    def test_out_of_range_page_redirects_to_last(self, auth_client):
        _seed(auth_client)
        response = auth_client.get("/bookmarks?page=9", headers={"Accept": "text/html"})
        assert response.status_code == 303
        assert response.headers["location"] == "/bookmarks"  # 单页时回退第 1 页（无 page 参数）

    def test_empty_result_keeps_page_one(self, auth_client):
        _seed(auth_client)
        response = auth_client.get(
            "/bookmarks?q=no-such-item&page=5", headers={"Accept": "text/html"}
        )
        assert response.status_code == 200
        assert "没有符合条件的书签" in response.text

    def test_htmx_fragment_returns_hx_redirect_on_clamp(self, auth_client):
        _seed(auth_client)
        response = auth_client.get(
            "/api/bookmarks?page=9",
            headers={"Accept": "text/html", "HX-Request": "true"},
        )
        assert response.status_code == 200
        assert response.headers.get("HX-Redirect") == "/bookmarks"

    def test_htmx_fragment_renders_items(self, auth_client):
        _seed(auth_client)
        response = auth_client.get(
            "/api/bookmarks",
            headers={"Accept": "text/html", "HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "bookmark-item" in response.text
        assert "共 3 条" in response.text

    def test_new_bookmark_modal_fragment(self, auth_client):
        response = auth_client.get("/api/bookmarks/new-modal", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert "新增书签" in response.text
        assert "bookmark-form" in response.text

    def test_edit_modal_roundtrip_prefill(self, auth_client, db_session):
        _seed(auth_client)
        bookmark = db_session.query(Bookmark).order_by(Bookmark.id).first()
        response = auth_client.get(f"/api/bookmarks/{bookmark.id}", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert "编辑书签" in response.text
        assert bookmark.title in response.text


class TestCategoryFilterUi:
    """书签页分类筛选组件（BM-V1-406）：完整侧栏树、选中态与子树命中。"""

    def _setup(self, auth_client, db_session):
        from app.services.category_service import CategoryService
        from app.services.versioned import get_tree_revision

        svc = CategoryService(db_session)
        work = svc.create("工作", None, get_tree_revision(db_session))
        db_session.flush()
        dev = svc.create("开发", work.id, get_tree_revision(db_session))
        db_session.flush()
        db_session.commit()
        auth_client.post(
            "/api/bookmarks",
            json={
                "title": "后端文章",
                "url": "https://example.com/back",
                "category_id": dev.id,
            },
            headers={"X-CSRF-Token": auth_client.cookies.get("bookmark_csrf", "")},
        )
        return work.id, dev.id

    @staticmethod
    def _sort_form(html: str) -> str:
        """提取排序表单内部的 hidden 字段区域。"""
        match = re.search(
            r'<form method="get" action="/bookmarks" class="filter-form-inline">(.*?)</form>',
            html,
            re.S,
        )
        assert match is not None, "未找到排序表单"
        return match.group(1)

    def test_selected_category_highlighted(self, auth_client, db_session):
        work_id, dev_id = self._setup(auth_client, db_session)
        response = auth_client.get(
            f"/bookmarks?category={work_id}", headers={"Accept": "text/html"}
        )
        assert response.status_code == 200
        # 整树展开，且仅当前筛选分类命中高亮
        assert response.text.count('class="cat-link cat-selected"') == 1
        assert f'href="/bookmarks?category={work_id}"' in response.text
        assert f'href="/bookmarks?category={dev_id}"' in response.text

    def test_sidebar_counts_include_descendants(self, auth_client, db_session):
        work_id, dev_id = self._setup(auth_client, db_session)
        response = auth_client.get("/bookmarks", headers={"Accept": "text/html"})
        assert response.status_code == 200
        # 工作节点含后代计数=1（书签在子分类“开发”下），开发自身=1
        count_cell = '<span class="cat-count" title="含后代书签总数">1</span>'
        assert response.text.count(count_cell) == 2

    def test_sort_form_keeps_category_and_favorite(self, auth_client, db_session):
        work_id, dev_id = self._setup(auth_client, db_session)
        auth_client.post(
            "/api/bookmarks",
            json={
                "title": "收藏的开发资料",
                "url": "https://example.com/fav",
                "category_id": dev_id,
                "is_favorite": True,
            },
            headers={"X-CSRF-Token": auth_client.cookies.get("bookmark_csrf", "")},
        )
        response = auth_client.get(
            f"/bookmarks?category={work_id}&favorite=1", headers={"Accept": "text/html"}
        )
        assert response.status_code == 200
        assert "共 1 条" in response.text  # 收藏 ∩ 分类子树
        sort_form = self._sort_form(response.text)
        assert f'name="category" value="{work_id}"' in sort_form
        assert 'name="favorite" value="1"' in sort_form

    def test_sort_form_keeps_tag_filter(self, auth_client, db_session):
        _seed(auth_client)
        tag = db_session.query(Tag).filter(Tag.name == "tag0").first()
        assert tag is not None
        response = auth_client.get(
            f"/bookmarks?tag={tag.id}", headers={"Accept": "text/html"}
        )
        assert response.status_code == 200
        sort_form = self._sort_form(response.text)
        assert f'name="tag" value="{tag.id}"' in sort_form

    def test_sort_form_keeps_page_size(self, auth_client):
        response = auth_client.get(
            "/bookmarks?page_size=64", headers={"Accept": "text/html"}
        )
        assert response.status_code == 200
        sort_form = self._sort_form(response.text)
        assert 'name="page_size" value="64"' in sort_form

    def test_full_category_sidebar(self, auth_client, db_session):
        self._setup(auth_client, db_session)
        response = auth_client.get("/bookmarks", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert 'class="category-sidebar"' in response.text
        assert 'aria-label="完整分类树"' in response.text
        assert "工作" in response.text
        assert "开发" in response.text
        assert 'class="chip-more"' not in response.text
        assert response.text.count("<details open>") >= 1

    def test_category_filter_includes_descendants(self, auth_client, db_session):
        work_id, dev_id = self._setup(auth_client, db_session)
        response = auth_client.get(
            f"/bookmarks?category={work_id}", headers={"Accept": "text/html"}
        )
        assert response.status_code == 200
        assert "共 1 条" in response.text  # 子树命中
        assert "全部" in response.text and "/" in response.text  # 面包屑区域存在

    def test_refresh_keeps_filter_state(self, auth_client, db_session):
        work_id, dev_id = self._setup(auth_client, db_session)
        first = auth_client.get(f"/bookmarks?category={work_id}", headers={"Accept": "text/html"})
        assert first.status_code == 200
        # 服务端页面直接反映状态（等价刷新后不丢）
        assert "工作" in first.text
        assert "共 1 条" in first.text

    def test_uncategorized_not_shown_as_special_category(self, auth_client, db_session):
        """未分类是虚拟筛选项：树中不应出现名为“未分类”的分类节点。"""
        self._setup(auth_client, db_session)
        response = auth_client.get("/bookmarks", headers={"Accept": "text/html"})
        assert response.status_code == 200
        tree_html = response.text.split('aria-label="完整分类树"', 1)[1]
        assert "未分类" not in tree_html
