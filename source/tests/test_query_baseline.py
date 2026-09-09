"""查询与性能基线（BM-V1-108）。

- EXPLAIN QUERY PLAN 测试夹具：关键列表查询走预期索引；
- 标签联表查询按书签去重（EXISTS 语义，无重复行）；
- 种子数据工具可注入隔离测试库（不写生产目录）。
"""

from __future__ import annotations

import pytest
from scripts.seed_data import seed
from sqlalchemy import text


@pytest.fixture
def seeded_db(db):
    """在隔离库注入少量样本后返回（让规划器基于真实基数选择索引）。"""
    seed(db, rows=600)
    return db


def _explain(db, sql: str, params: dict | None = None) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(text(f"EXPLAIN QUERY PLAN {sql}"), params or {}).all()
    return [" ".join(str(value) for value in row) for row in rows]


def test_default_list_query_uses_compound_index(db):
    """正常列表默认排序 (deleted_at, created_at, id) 应使用 ix_bookmarks_list。"""
    plan = _explain(
        db,
        "SELECT id FROM bookmarks WHERE deleted_at IS NULL"
        " ORDER BY created_at DESC, id DESC LIMIT 50",
    )
    assert any("ix_bookmarks_list" in line for line in plan), "\n".join(plan)


def test_trash_query_uses_deleted_at_index(db):
    plan = _explain(
        db,
        "SELECT id FROM bookmarks WHERE deleted_at IS NOT NULL"
        " ORDER BY deleted_at DESC, id DESC LIMIT 50",
    )
    assert any("ix_bookmarks_deleted_at" in line for line in plan), "\n".join(plan)


def test_category_filter_uses_index(seeded_db):
    plan = _explain(
        seeded_db,
        "SELECT id FROM bookmarks WHERE deleted_at IS NULL AND category_id IN (:c1, :c2)"
        " ORDER BY created_at DESC LIMIT 50",
        {"c1": 1, "c2": 2},
    )
    joined = "\n".join(plan)
    # 关键列表查询必须走索引，不得全表扫描
    assert "SEARCH bookmarks USING" in joined, joined
    assert "SCAN bookmarks" not in joined


def test_tag_join_deduplicated_by_exists(seeded_db):
    """标签筛选必须按书签 ID 去重：EXISTS 子查询方案不会产生重复行。"""
    sql = (
        "SELECT b.id FROM bookmarks b WHERE b.deleted_at IS NULL"
        " AND EXISTS (SELECT 1 FROM bookmark_tags bt"
        " JOIN tags t ON t.id = bt.tag_id WHERE bt.bookmark_id = b.id AND t.normalized_name LIKE :q)"
        " ORDER BY b.created_at DESC, b.id DESC LIMIT 50"
    )
    plan = _explain(seeded_db, sql, {"q": "%python%"})
    joined = "\n".join(plan)
    # 子查询走 bookmark_tags 主键（覆盖）索引，无全表扫描；EXISTS 方案天然去重
    assert "sqlite_autoindex_bookmark_tags_1" in joined, joined
    assert "SCAN bookmark_tags" not in joined
    assert "SCAN bt" not in joined


def test_no_n_plus_one_batch_loading(db_session):
    """列表书签的标签/分类必须批量加载：一次列表查询 + selectin，不得逐条回查。"""
    from app.models.bookmark import Bookmark
    from app.models.category import Category
    from app.models.tag import Tag
    from sqlalchemy import event, select
    from sqlalchemy.orm import selectinload

    parent = Category(name="根", normalized_name="根")
    db_session.add(parent)
    db_session.flush()
    tags = [Tag(name=f"t{i}", normalized_name=f"t{i}") for i in range(3)]
    db_session.add_all(tags)
    db_session.flush()
    for index in range(5):
        bookmark = Bookmark(
            title=f"b{index}",
            url=f"https://e.com/{index}",
            normalized_url=f"https://e.com/{index}",
            category_id=parent.id,
            tags=tags,
        )
        db_session.add(bookmark)
    db_session.commit()

    query_count = 0

    def _count(conn, cursor, statement, parameters, context, executemany):
        nonlocal query_count
        query_count += 1

    event.listen(db_session.get_bind(), "before_cursor_execute", _count)
    try:
        rows = list(
            db_session.scalars(
                select(Bookmark)
                .where(Bookmark.deleted_at.is_(None))
                .options(selectinload(Bookmark.tags))
                .order_by(Bookmark.id)
            )
        )
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", _count)
    assert len(rows) == 5
    assert sum(len(b.tags) for b in rows) == 15  # 关联完整可用
    assert query_count == 2  # 1 次列表 + 1 次 selectin（无 N+1）


def test_seed_tool_writes_only_explicit_database(db):
    """种子工具在隔离测试库生成可复现数据；不触碰生产目录。"""
    counts = seed(db, rows=300)
    assert counts["bookmarks"] == 300
    assert counts["tags"] == 15
    with db.connect() as conn:
        bookmarks = conn.execute(text("SELECT COUNT(*) FROM bookmarks")).scalar()
        links = conn.execute(text("SELECT COUNT(*) FROM bookmark_tags")).scalar()
        assert bookmarks == 300
        assert links == 300 * 3
