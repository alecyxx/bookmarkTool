"""书签查询测试（BM-V1-304/305/306）。"""

from __future__ import annotations

import pytest
from app.models.bookmark import Bookmark
from app.models.tag import Tag
from app.services.bookmark_service import BookmarkService, escape_like
from app.services.category_service import CategoryService
from app.services.versioned import get_tree_revision
from sqlalchemy import select


def _make_bookmark(
    db_session, title, url, *, category_id=None, tags=None, description="", favorite=False
):
    bookmark = Bookmark(
        title=title,
        url=url,
        normalized_url=url.lower(),
        description=description,
        category_id=category_id,
        is_favorite=favorite,
    )
    db_session.add(bookmark)
    db_session.flush()
    for tag_name in tags or []:
        from app.services.normalize import normalize_name

        tag = db_session.scalar(select(Tag).where(Tag.normalized_name == normalize_name(tag_name)))
        if tag is None:
            tag = Tag(name=tag_name, normalized_name=normalize_name(tag_name))
            db_session.add(tag)
            db_session.flush()
        bookmark.tags.append(tag)
    db_session.flush()
    return bookmark


@pytest.fixture
def sample_tree(db_session):
    svc = CategoryService(db_session)
    work = svc.create("工作", None, get_tree_revision(db_session))
    db_session.flush()
    dev = svc.create("开发", work.id, get_tree_revision(db_session))
    db_session.flush()
    back = svc.create("后端", dev.id, get_tree_revision(db_session))
    db_session.flush()
    ai = svc.create("AI", None, get_tree_revision(db_session))
    db_session.flush()
    db_session.commit()
    return {"work": work.id, "dev": dev.id, "back": back.id, "ai": ai.id}


class TestSortingAndPagination:
    def _seed(self, db_session, count: int = 30):
        bookmarks = []
        for index in range(count):
            bookmarks.append(
                _make_bookmark(
                    db_session,
                    f"标题 {index:02d}",
                    f"https://example.com/p{index:02d}",
                    favorite=index % 3 == 0,
                )
            )
        db_session.commit()
        return bookmarks

    def test_default_sort_created_desc_stable(self, db_session):
        self._seed(db_session)
        svc = BookmarkService(db_session)
        page = svc.list_page(page=1, page_size=25)
        assert page.total == 30
        assert len(page.items) == 25
        # 相同时间戳按 id 稳定倒序：无重复无漏项
        ids = [b.id for b in page.items]
        page2 = svc.list_page(page=2, page_size=25)
        ids2 = [b.id for b in page2.items]
        assert len(ids2) == 5
        assert len(set(ids + ids2)) == 30

    def test_page_out_of_range_clamped(self, db_session):
        self._seed(db_session)
        svc = BookmarkService(db_session)
        page = svc.list_page(page=99, page_size=25)
        assert page.page == 2
        assert page.max_page == 2
        empty = svc.list_page(page=5, page_size=25)  # total>0 时钳到最后一页
        assert empty.page == 2

    def test_invalid_params_fallback(self, db_session):
        self._seed(db_session)
        svc = BookmarkService(db_session)
        page = svc.list_page(page=0, page_size=13, sort="bogus")
        assert page.page == 1
        assert page.page_size == 50
        assert page.total == 30

    def test_all_sorts_valid(self, db_session):
        self._seed(db_session)
        svc = BookmarkService(db_session)
        for sort in (
            "created_desc",
            "created_asc",
            "updated_desc",
            "title_asc",
            "title_desc",
            "favorite_first",
        ):
            page = svc.list_page(sort=sort, page_size=100)
            assert len(page.items) == 30
        favorite_first = svc.list_page(sort="favorite_first", page_size=100)
        assert favorite_first.items[0].is_favorite is True


class TestSearch:
    def _seed(self, db_session, sample_tree):
        ai = sample_tree["ai"]
        back = sample_tree["back"]
        _make_bookmark(
            db_session,
            "Python 教程",
            "https://python.org/tutorial",
            category_id=ai,
            tags=["Python", "文档"],
            description="官方教程",
        )
        _make_bookmark(
            db_session,
            "Docker 文档",
            "https://docker.com",
            category_id=back,
            tags=["DevOps"],
            description="",
        )
        _make_bookmark(
            db_session, "FastAPI", "https://fastapi.tiangolo.com", category_id=None, tags=["API"]
        )
        db_session.commit()

    def test_search_title_url_desc(self, db_session, sample_tree):
        self._seed(db_session, sample_tree)
        svc = BookmarkService(db_session)
        assert svc.list_page(q="Python").total == 1
        assert svc.list_page(q="docker.com").total == 1
        assert svc.list_page(q="官方教程").total == 1

    def test_search_case_and_like_escape(self, db_session, sample_tree):
        self._seed(db_session, sample_tree)
        svc = BookmarkService(db_session)
        assert svc.list_page(q="PYTHON").total == 1
        assert svc.list_page(q="%").total == 0  # LIKE 特殊字符被转义
        assert svc.list_page(q="_").total == 0

    def test_search_category_ancestor_path(self, db_session, sample_tree):
        self._seed(db_session, sample_tree)
        svc = BookmarkService(db_session)
        # “工作”命中其祖先路径包含“工作”的后代分类书签（后端 -> Docker）
        assert svc.list_page(q="工作").total == 1
        assert svc.list_page(q="后端").total == 1

    def test_search_tag_substring(self, db_session, sample_tree):
        self._seed(db_session, sample_tree)
        svc = BookmarkService(db_session)
        assert svc.list_page(q="thon").total == 1  # 标签 Python 子串
        assert svc.list_page(q="Dev").total == 1  # 标签 DevOps 子串

    def test_trash_excluded_from_search(self, db_session, sample_tree):
        self._seed(db_session, sample_tree)
        from app.database import now_utc

        bookmark = db_session.scalar(select(Bookmark).where(Bookmark.title == "Python 教程"))
        bookmark.deleted_at = now_utc()
        db_session.commit()
        svc = BookmarkService(db_session)
        assert svc.list_page(q="Python").total == 0


class TestFilters:
    def _seed(self, db_session, sample_tree):
        work, dev, back = sample_tree["work"], sample_tree["dev"], sample_tree["back"]
        _make_bookmark(db_session, "W1", "https://e.com/w1", category_id=work)
        _make_bookmark(db_session, "D1", "https://e.com/d1", category_id=dev)
        _make_bookmark(db_session, "B1", "https://e.com/b1", category_id=back, tags=["T1"])
        _make_bookmark(db_session, "U1", "https://e.com/u1", favorite=True)
        db_session.commit()

    def test_category_filter_includes_descendants(self, db_session, sample_tree):
        self._seed(db_session, sample_tree)
        svc = BookmarkService(db_session)
        assert svc.list_page(category_id=sample_tree["work"]).total == 3  # 含开发/后端后代
        assert svc.list_page(category_id=sample_tree["back"]).total == 1
        assert svc.list_page(category_id=sample_tree["ai"]).total == 0

    def test_tag_filter_and_combined_and(self, db_session, sample_tree):
        self._seed(db_session, sample_tree)
        svc = BookmarkService(db_session)
        tag = db_session.scalar(select(Tag).where(Tag.normalized_name == "t1"))
        assert svc.list_page(tag_id=tag.id).total == 1
        # 组合条件 AND：分类 工作 且 标签 T1
        assert svc.list_page(category_id=sample_tree["work"], tag_id=tag.id).total == 1
        # 收藏筛选
        assert svc.list_page(favorite=True).total == 1
        # 组合：搜索 + 收藏
        assert svc.list_page(q="u1", favorite=True).total == 1

    def test_trash_include(self, db_session, sample_tree):
        self._seed(db_session, sample_tree)
        from app.database import now_utc

        bookmark = db_session.scalar(select(Bookmark).where(Bookmark.title == "U1"))
        bookmark.deleted_at = now_utc()
        db_session.commit()
        svc = BookmarkService(db_session)
        assert svc.list_page().total == 3
        assert svc.list_page(include_trash=True).total == 1


def test_escape_like():
    assert escape_like("50%_\\off") == r"50\%\_\\off"
    assert escape_like("普通中文") == "普通中文"
